import serial
import time

ser = serial.Serial("/dev/serial0", 115200, timeout=0.5)
print("listening on /dev/serial0 for 13s...", flush=True)
end = time.time() + 13
buf = b""
while time.time() < end:
    chunk = ser.read(256)
    if chunk:
        buf += chunk
        print("got:", chunk.hex(), flush=True)
label = buf.hex() if buf else "(none)"
print(f"TOTAL: {len(buf)} bytes  {label}")
ser.close()
