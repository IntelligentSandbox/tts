# start http server python
import http.server
import socketserver

if __name__ == "__main__":
    with socketserver.TCPServer(("0.0.0.0", 8080), http.server.SimpleHTTPRequestHandler) as httpd:
        print("Running on 0.0.0.0:8000")
        httpd.serve_forever()
