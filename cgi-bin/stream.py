#!/usr/bin/env python3
import os
import sys
import re
import urllib.parse

# Force binary mode for stdout on Windows
if sys.platform.startswith('win'):
    import msvcrt
    msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)

query = os.environ.get('QUERY_STRING', '')
match = re.search(r'file=([^&]+)', query)

if not match:
    sys.stdout.buffer.write(b"Status: 400 Bad Request\r\nContent-Type: text/plain\r\n\r\nFile parameter missing.\r\n")
    sys.exit(0)

# Decode URL encoded characters (%2F -> /)
raw_file = match.group(1)
rel_path = urllib.parse.unquote(raw_file)

# Prevent directory traversal
rel_path = rel_path.replace('..', '').strip('/')
file_path = os.path.join('/mnt/sda5/nvr', rel_path)

if not os.path.exists(file_path) or not os.path.isfile(file_path):
    sys.stdout.buffer.write(b"Status: 404 Not Found\r\nContent-Type: text/plain\r\n\r\nFile not found.\r\n")
    sys.exit(0)

file_size = os.path.getsize(file_path)
http_range = os.environ.get('HTTP_RANGE', '')

start = 0
end = file_size - 1

if http_range:
    range_match = re.search(r'bytes=(\d*)-(\d*)', http_range)
    if range_match:
        s_str, e_str = range_match.groups()
        if s_str:
            start = int(s_str)
        if e_str:
            end = int(e_str)
        if end >= file_size:
            end = file_size - 1

content_length = (end - start) + 1

if http_range:
    headers = [
        "Status: 206 Partial Content",
        "Accept-Ranges: bytes",
        f"Content-Range: bytes {start}-{end}/{file_size}",
        "Content-Type: video/mp4",
        f"Content-Length: {content_length}",
        "Connection: keep-alive",
        ""
    ]
else:
    headers = [
        "Status: 200 OK",
        "Accept-Ranges: bytes",
        "Content-Type: video/mp4",
        f"Content-Length: {file_size}",
        "Connection: keep-alive",
        ""
    ]

try:
    sys.stdout.buffer.write(("\r\n".join(headers) + "\r\n").encode('utf-8'))
    sys.stdout.buffer.flush()
except Exception:
    pass

chunk_size = 64 * 1024
bytes_to_send = content_length

try:
    with open(file_path, 'rb') as f:
        f.seek(start)
        while bytes_to_send > 0:
            read_len = min(chunk_size, bytes_to_send)
            data = f.read(read_len)
            if not data:
                break
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
            bytes_to_send -= len(data)
except Exception:
    pass
