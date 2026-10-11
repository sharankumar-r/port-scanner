import argparse
import json
import os
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import HTTPServer, SimpleHTTPRequestHandler


def resolve_target(target: str) -> str:
    try:
        return socket.gethostbyname(target)
    except socket.gaierror:
        return None


def get_service_name(port: int) -> str:
    try:
        return socket.getservbyport(port, "tcp")
    except (OSError, socket.error):
        return "unknown"


def check_port(target_ip: str, port: int, timeout: float = 1.0):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            if s.connect_ex((target_ip, port)) == 0:
                service = get_service_name(port)
                return port, service
    except (socket.timeout, OSError):
        pass
    return None


def scan_ports(target: str, start_port: int = 1, end_port: int = 1024, threads: int = 100, timeout: float = 1.0):
    target_ip = resolve_target(target)
    if not target_ip:
        print(f"[!] Error: Could not resolve hostname '{target}'")
        return []

    print("=" * 60)
    print(f"Target:       {target} ({target_ip})")
    print(f"Port Range:   {start_port} - {end_port}")
    print(f"Threads:      {threads}")
    print("=" * 60)
    print(f"{'PORT':<10} {'STATE':<10} {'SERVICE'}")
    print("-" * 60)

    start_time = time.time()
    open_ports = []

    try:
        with ThreadPoolExecutor(max_workers=threads) as executor:
            futures = [
                executor.submit(check_port, target_ip, port, timeout)
                for port in range(start_port, end_port + 1)
            ]
            for future in futures:
                result = future.result()
                if result:
                    port, service = result
                    open_ports.append({"port": port, "service": service})
                    print(f"{f'{port}/tcp':<10} {'open':<10} {service}")

    except KeyboardInterrupt:
        print("\n[!] Scan aborted by user.")
        return open_ports

    duration = time.time() - start_time
    print("=" * 60)
    print(f"Scan completed in {duration:.2f}s | {len(open_ports)} open port(s) found.")
    print("=" * 60)
    return open_ports


def parse_ports(port_arg: str):
    if "-" in port_arg:
        parts = port_arg.split("-")
        return int(parts[0]), int(parts[1])
    p = int(port_arg)
    return p, p


class WebHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "":
            self.path = "/index.html"
        elif self.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')
            return
        return super().do_GET()

    def do_POST(self):
        if self.path == "/api/scan":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode("utf-8"))
                target = data.get("target", "127.0.0.1")
                start_port = int(data.get("start_port", 1))
                end_port = int(data.get("end_port", 1024))
                threads = min(500, max(1, int(data.get("threads", 100))))
                timeout = float(data.get("timeout", 0.8))

                target_ip = resolve_target(target)
                if not target_ip:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": f"Cannot resolve {target}"}).encode("utf-8"))
                    return

                t0 = time.time()
                open_ports = []
                with ThreadPoolExecutor(max_workers=threads) as executor:
                    futures = [
                        executor.submit(check_port, target_ip, port, timeout)
                        for port in range(start_port, end_port + 1)
                    ]
                    for f in futures:
                        res = f.result()
                        if res:
                            open_ports.append({"port": res[0], "service": res[1]})

                elapsed = time.time() - t0
                response_payload = {
                    "target": target,
                    "ip": target_ip,
                    "scanned_count": end_port - start_port + 1,
                    "elapsed": elapsed,
                    "open_ports": open_ports,
                }

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response_payload).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()


def run_web_server(port=8080):
    current_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(current_dir)
    server_address = ("", port)
    httpd = HTTPServer(server_address, WebHandler)
    print("=" * 60)
    print(f" Port Scanner Web Dashboard running at: http://localhost:{port}")
    print(f" Press Ctrl+C to stop the server.")
    print("=" * 60)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[!] Stopping server.")
        httpd.server_close()


def main():
    parser = argparse.ArgumentParser(description="Fast Multi-threaded TCP Port Scanner")
    parser.add_argument("-t", "--target", help="Target IP or domain")
    parser.add_argument("-p", "--ports", default="1-1024", help="Port range (e.g. 1-1024 or 80)")
    parser.add_argument("-w", "--threads", type=int, default=100, help="Worker threads (default: 100)")
    parser.add_argument("--timeout", type=float, default=1.0, help="Socket timeout in seconds (default: 1.0)")
    parser.add_argument("--web", action="store_true", help="Launch interactive web dashboard")
    parser.add_argument("--web-port", type=int, default=8080, help="Port for web dashboard (default: 8080)")

    args = parser.parse_args()

    if args.web:
        run_web_server(port=args.web_port)
        return

    target = args.target
    if not target:
        target = input("Enter target (IP or domain): ").strip()
        if not target:
            print("[!] Target cannot be empty.")
            sys.exit(1)

    try:
        start_port, end_port = parse_ports(args.ports)
    except ValueError:
        print("[!] Invalid port format. Use START-END (e.g. 1-1024) or a single port.")
        sys.exit(1)

    scan_ports(target, start_port, end_port, threads=args.threads, timeout=args.timeout)


if __name__ == "__main__":
    main()
