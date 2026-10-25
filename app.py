# Python Helper Script for Valence Studio
# Run this script to start a local development server for Valence Studio website.

import http.server
import socketserver
import webbrowser
import os

PORT = 8000

class Handler(http.server.SimpleHTTPRequestHandler):
    pass

if __name__ == "__main__":
    web_dir = os.path.dirname(os.path.realpath(__file__))
    os.chdir(web_dir)
    
    print(f"==================================================")
    print(f"  VALENCE STUDIO - Local Server Running")
    print(f"  URL: http://localhost:{PORT}")
    print(f"==================================================")
    
    # Automatically open in web browser
    webbrowser.open(f"http://localhost:{PORT}")
    
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped successfully.")
