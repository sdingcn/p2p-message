import datetime
import select
import socket
import sys
import threading
import typing

PACKET_LENGTH = 128
HALT = False

def make_header(name: typing.Union[None, str] = None) -> str:
    if name is None:
        name = 'system'
    else:
        name = f'[{name}]'
    time_str = datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%d %H:%M:%S')
    return f'({name} {time_str}) '

class Neighbor:
    def __init__(self, so: socket.socket,
                 read_buffer: bytes, write_buffer: bytes):
        self.so = so
        self.read_buffer = read_buffer
        self.write_buffer = write_buffer

def pad_data(data: bytes) -> bytes:
    global PACKET_LENGTH
    if len(data) > PACKET_LENGTH:
        return data[:PACKET_LENGTH]
    else:
        return data + (b' ' * (PACKET_LENGTH - len(data)))

def listener(lock: threading.Lock, server_socket: socket.socket,
             neighbors: list[Neighbor]) -> None:
    global HALT
    server_socket.listen()
    while True:
        with lock:
            if HALT:
                return
        try:
            client_socket, _ = server_socket.accept()
        except socket.timeout:
            continue
        client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        client_socket.setblocking(False)
        with lock:
            neighbors.append(Neighbor(client_socket, b'', b''))
            sys.stderr.write(make_header() + 'accepted a new connection\n')

def relayer(lock: threading.Lock,
            neighbors: list[Neighbor]) -> None:
    global PACKET_LENGTH, HALT
    while True:
        with lock:
            if HALT:
                return
        with lock:
            socket_list = []
            for neighbor in neighbors:
                socket_list.append(neighbor.so)
        if socket_list:
        readable_list, writable_list, exceptional_list = select.select(
            socket_list, socket_list, socket_list, 0.1)
        # try reading
        dead_list = []
        new_reads = {}
        for so in readable_list:
            data = so.recv(PACKET_LENGTH)
            if data == b'':
                dead_list.append(so)
            else:
                new_reads[so.getsockname()] = data
        # save reading to buffers and try relaying
        with lock:
            for neighbor in neighbors:
                addr = neighbor.so.getsockname()
                if addr in new_reads:
                    neighbor.read_buffer += new_reads[addr]
                if len(neighbor.read_buffer) >= PACKET_LENGTH:
                    packet = neighbor.read_buffer[:PACKET_LENGTH]
                    print(packet.decode().strip())
                    neighbor.read_buffer = neighbor.read_buffer[PACKET_LENGTH:]
                    for other in neighbors:
                        other_addr = other.so.getsockname()
                        if other_addr != addr:
                            other.write_buffer += packet
        # try writing
        writes = {}
        with lock:
            for neighbor in neighbors:
                addr = neighbor.so.getsockname()
                if neighbor.write_buffer:
                    writes[addr] = neighbor.write_buffer  # try writing everything
                    neighbor.write_buffer = b''
        for so in writable_list:
            addr = so.getsockname()
            if addr in writes:
                sent = so.send(writes[addr])
                writes[addr] = writes[addr][sent:]
        # store back remaining parts
        with lock:
            for neighbor in neighbors:
                addr = neighbor.so.getsockname()
                if addr in writes:
                    neighbor.write_buffer = (writes[addr] + neighbor.write_buffer)
        # detect disconnections
        dead_addrs = set(
            [so.getsockname() for so in dead_list] + [so.getsockname() for so in exceptional_list])
        with lock:
            remaining_neighbors = []
            for neighbor in neighbors:
                addr = neighbor.so.getsockname()
                if addr in dead_addrs:
                    sys.stderr.write(make_header() + f'detected the disconnection of {addr}\n')
                else:
                    remaining_neighbors.append(neighbor)
            neighbors = remaining_neighbors

def start_node(name: str, my_addr: tuple[str, int],
               inviter_addr: typing.Union[None, tuple[str, int]]) -> None:
    global HALT
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.setblocking(False)
    server_socket.bind(my_addr)
    neighbors = []
    if inviter_addr:
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        client_socket.settimeout(5)
        try:
            client_socket.connect(inviter_addr)
        except socket.timeout:
            sys.exit(make_header() + 'connection to the inviter timed out')
        sys.stderr.write(make_header() + 'connected to the inviter\n')
        client_socket.setblocking(False)
        neighbors.append(Neighbor(client_socket, b'', b''))
    lock = threading.Lock()
    listener_thread = threading.Thread(target = listener,
                      args = (lock, server_socket, neighbors))
    listener_thread.start()
    relayer_thread = threading.Thread(target = relayer,
                     args = (lock, neighbors))
    relayer_thread.start()
    while True:
        content = input()
        if content == '':
            with lock:
                HALT = True
            listener_thread.join()
            relayer_thread.join()
            return
        else:
            message = make_header(name) + content
            data = pad_data(message.encode())
            with lock:
                print(message)
                for neighbor in neighbors:
                    neighbor.write_buffer += data

if __name__ == '__main__':
    if (len(sys.argv) != 4) and (len(sys.argv) != 6):
        sys.exit(make_header() + f'python3 {sys.argv[0]}'
                  ' <name> <my-ip> <my-port> [inviter-ip] [inviter-port]')
    name = sys.argv[1]
    my_addr = (sys.argv[2], int(sys.argv[3]))
    inviter_addr = None if len(sys.argv) == 4 else (sys.argv[4],
                                                    int(sys.argv[5]))
    start_node(name, my_addr, inviter_addr)
