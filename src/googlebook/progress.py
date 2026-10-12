"""Real completed work plus a heartbeat while a disk request blocks."""
import sys
import threading
import time


class Progress:
    def __init__(self, total, stream=None, interval=1):
        self.stream=stream or sys.stdout;self.total=max(total,1);self.done=0
        self.interval=interval
        self.operation='Starting';self.start=self.changed=time.monotonic()
        self.lock=threading.RLock();self.stop=threading.Event();self.drawn=False
        self.thread=threading.Thread(target=self._heartbeat,daemon=True)

    def __enter__(self):self.thread.start();self.render();return self

    def advance(self, amount, operation):
        with self.lock:
            if amount<0 or self.done+amount>self.total:raise ValueError('Invalid completed-work count')
            self.done+=amount;self.operation=operation
            if amount:self.changed=time.monotonic()

    def render(self):
        with self.lock:
            now=time.monotonic();fraction=self.done/self.total;filled=int(fraction*24)
            unit,scale=('GiB',1024**3) if self.total>=1024**3 else ('MiB',1024**2)
            line=f'[{"#"*filled}{"-"*(24-filled)}] {100*fraction:5.1f}%  {self.done/scale:.1f}/{self.total/scale:.1f} {unit}'
            elapsed=int(now-self.start);detail=f'{self.operation} | {elapsed//60:02d}:{elapsed%60:02d}'
            if now-self.changed>=5:detail+=f' | waiting for I/O {int(now-self.changed)}s'
            if self.stream.isatty():
                if self.drawn:self.stream.write('\x1b[2A')
                self.stream.write('\x1b[2K\r'+line[:79]+'\n\x1b[2K\r'+detail[:79]+'\n')
            else:self.stream.write(line+' | '+detail+'\n')
            self.stream.flush();self.drawn=True

    def _heartbeat(self):
        while not self.stop.wait(self.interval):self.render()

    def __exit__(self,*_):
        self.stop.set();self.thread.join(timeout=2);self.render()
