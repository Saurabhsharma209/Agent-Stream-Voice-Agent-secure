#!/usr/bin/env python3
"""
Launcher: Main API Server
Starts the FastAPI application running on port 5000 via Uvicorn.
Provides full REST and interactive Swagger documentation capabilities.
"""

import sys
from pathlib import Path

# Add current directory to python path
sys.path.insert(0, str(Path(__file__).parent))

import uvicorn
from config import Config

def main():
    print("\n" + "=" * 60)
    print("🚀 Voice AI Agent Gateway Server")
    print("=" * 60)
    print(f"📍 Host: {Config.SERVER_HOST}")
    print(f"🔌 Port: {Config.SERVER_PORT}")
    
    # Determine printable URL
    host_display = "localhost" if Config.SERVER_HOST == "0.0.0.0" else Config.SERVER_HOST
    print(f"🌐 Landing Portal: http://{host_display}:{Config.SERVER_PORT}/")
    print(f"⚡ Swagger Documentation: http://{host_display}:{Config.SERVER_PORT}/docs")
    print(f"📋 ReDoc Documentation: http://{host_display}:{Config.SERVER_PORT}/redoc")
    print("=" * 60 + "\n")
    
    try:
        # Run uvicorn server programmatically
        uvicorn.run(
            "api_gateway:app",
            host=Config.SERVER_HOST,
            port=Config.SERVER_PORT,
            reload=False,
            log_level="info"
        )
    except KeyboardInterrupt:
        print("\n👋 Gateway server stopped gracefully.")
    except Exception as e:
        print(f"\n❌ Error starting server: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
