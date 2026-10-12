"""Strict standard GPT handling: 512-byte sectors, 128 entries, both CRCs."""
import base64
import hashlib
import struct
import uuid
import zlib

SECTOR = 512
TABLE_BYTES = 16384
ESP_TYPE = 'c12a7328-f81f-11d2-ba4b-00a0c93ec93b'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def regions(size):
    if size % SECTOR or size < 1024**2:
        raise ValueError('Unsupported disk geometry')
    return [(0, 512), (512, 512), (1024, TABLE_BYTES),
            (size-16896, TABLE_BYTES), (size-512, 512)]


def capture(read, size):
    return [dict(offset=at, data=base64.b64encode(read(at, n)).decode())
            for at, n in regions(size)]


def raw(snapshot):
    return [base64.b64decode(r['data'], validate=True) for r in snapshot['regions']]


def inspect(read, size):
    saved = capture(read, size)
    mbr, header, table, other_table, other = [base64.b64decode(r['data']) for r in saved]
    final = size//512-1
    ids = []
    for data, lba, peer, table_lba in [(header, 1, final, 2), (other, final, 1, final-32)]:
        if len(data) != 512 or data[:8] != b'EFI PART':
            raise ValueError('A valid GPT is required for alongside installation')
        version, length, crc, reserved = struct.unpack_from('<IIII', data, 8)
        check = bytearray(data[:92]);struct.pack_into('<I', check, 16, 0)
        if (version, length, reserved) != (0x10000, 92, 0) or zlib.crc32(check) != crc:
            raise ValueError('Invalid GPT header CRC/format')
        if struct.unpack_from('<QQQQ', data, 24) != (lba, peer, 34, final-33):
            raise ValueError('Unsupported GPT usable-sector geometry')
        if struct.unpack_from('<QIII', data, 72) != (table_lba, 128, 128, zlib.crc32(table)):
            raise ValueError('Unsupported or corrupt GPT entry table')
        ids.append(str(uuid.UUID(bytes_le=data[56:72])))
    if len(table) != TABLE_BYTES or table != other_table or ids[0] != ids[1]:
        raise ValueError('GPT copies disagree; automatic repair is refused')
    if mbr[510:] != b'\x55\xaa' or mbr[450] != 0xee:
        raise ValueError('Missing protective MBR')
    if any(mbr[446+i*16+4] for i in (1, 2, 3)):
        raise ValueError('Hybrid MBR is not supported')
    parts = []
    for index in range(128):
        entry = table[index*128:(index+1)*128]
        if not any(entry[:16]):
            if any(entry):raise ValueError('Nonempty unused GPT entry')
            continue
        first, last, attributes = struct.unpack_from('<QQQ', entry, 32)
        if not 34 <= first <= last <= final-33:
            raise ValueError('Partition outside usable disk sectors')
        parts.append(dict(number=index+1, name=entry[56:].decode('utf-16le').rstrip('\0'),
                          type=str(uuid.UUID(bytes_le=entry[:16])),
                          partuuid=str(uuid.UUID(bytes_le=entry[16:32])),
                          offset=first*512, bytes=(last-first+1)*512, attributes=attributes))
    ordered = sorted((p['offset'], p['offset']+p['bytes']) for p in parts)
    if any(a[1] > b[0] for a, b in zip(ordered, ordered[1:])):
        raise ValueError('Overlapping partitions')
    if len({p['partuuid'] for p in parts}) != len(parts) or any(p['partuuid']==str(uuid.UUID(int=0)) for p in parts):
        raise ValueError('Invalid partition UUIDs')
    return dict(disk_bytes=size, disk_guid=ids[0], partitions=parts, regions=saved)


def assemble(size, parts, original=None):
    final = size//512-1
    table = bytearray(raw(original)[2]) if original else bytearray(TABLE_BYTES)
    disk_id = uuid.UUID(original['disk_guid']) if original else uuid.uuid4()
    for p in parts:
        index = (p['number']-1)*128
        if not 0 <= index < TABLE_BYTES or any(table[index:index+128]):
            raise ValueError('Partition slot already used or outside GPT')
        if p['offset'] % 512 or p['bytes'] % 512 or p['bytes'] <= 0:
            raise ValueError('Invalid partition alignment/length')
        entry = bytearray(128)
        entry[:16] = uuid.UUID(p['type']).bytes_le
        entry[16:32] = uuid.UUID(p['partuuid']).bytes_le
        struct.pack_into('<QQQ', entry, 32, p['offset']//512, (p['offset']+p['bytes'])//512-1, p.get('attributes', 0))
        label = p['name'].encode('utf-16le')
        if len(label)>72:raise ValueError('Partition label too long')
        entry[56:56+len(label)] = label
        table[index:index+128] = entry
    mbr = bytearray(raw(original)[0]) if original else bytearray(512)
    if not original:
        mbr[450]=0xee;struct.pack_into('<II', mbr, 454, 1, min(final, 0xffffffff));mbr[510:]=b'\x55\xaa'
    def header(lba, peer, at):
        data=bytearray(512);data[:8]=b'EFI PART'
        struct.pack_into('<IIII', data, 8, 0x10000, 92, 0, 0)
        struct.pack_into('<QQQQ', data, 24, lba, peer, 34, final-33)
        data[56:72]=disk_id.bytes_le
        struct.pack_into('<QIII', data, 72, at, 128, 128, zlib.crc32(table))
        struct.pack_into('<I', data, 16, zlib.crc32(data[:92]))
        return bytes(data)
    values=[bytes(mbr), header(1, final, 2), bytes(table), bytes(table), header(final, 1, final-32)]
    mapping=dict(zip(regions(size), values))
    return inspect(lambda at,n:mapping[(at,n)], size)
