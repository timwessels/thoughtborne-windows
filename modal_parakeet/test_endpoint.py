"""
Test script for the Modal Parakeet endpoint.

Usage:
    # Test with a local audio file
    python modal_parakeet/test_endpoint.py test_audio.mp3

    # Test with explicit endpoint URL
    python modal_parakeet/test_endpoint.py test_audio.mp3 --url https://your-endpoint.modal.run

    # Test with .env file (reads MODAL_ENDPOINT_URL)
    python modal_parakeet/test_endpoint.py test_audio.mp3

This script is completely independent from Thoughtborne - it just sends
an audio file to the Modal endpoint and prints the result.
"""

import sys
import os
import time
import argparse
import requests


def get_endpoint_url(args_url=None):
    """Get endpoint URL from args, .env, or environment."""
    if args_url:
        return args_url

    # Try environment variable
    url = os.getenv("MODAL_ENDPOINT_URL")
    if url:
        return url

    # Try .env file
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("MODAL_ENDPOINT_URL="):
                    url = line.split("=", 1)[1].strip().strip("'\"")
                    if url:
                        return url

    return None


def test_transcription(audio_path, endpoint_url):
    """Send audio file to endpoint and print result."""
    if not os.path.exists(audio_path):
        print(f"ERROR: Audio file not found: {audio_path}")
        return False

    file_size = os.path.getsize(audio_path)
    print(f"Audio file: {audio_path} ({file_size / 1024:.1f} KB)")
    print(f"Endpoint:   {endpoint_url}")
    print()

    # Read audio file
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    # Determine content type
    ext = os.path.splitext(audio_path)[1].lower()
    content_types = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
        ".m4a": "audio/mp4",
    }
    content_type = content_types.get(ext, "application/octet-stream")

    # Send request
    print(f"Sending {len(audio_bytes)} bytes ({content_type})...")
    start = time.time()

    try:
        response = requests.post(
            endpoint_url,
            data=audio_bytes,
            headers={"Content-Type": content_type},
            timeout=120,
        )

        elapsed = time.time() - start

        if response.status_code != 200:
            print(f"ERROR: HTTP {response.status_code}")
            print(f"Response: {response.text[:500]}")
            return False

        result = response.json()
        text = result.get("text", "")
        server_time = result.get("processing_time", "?")
        error = result.get("error", "")

        print(f"\n{'='*60}")
        print(f"Result:")
        print(f"{'='*60}")
        print(f"Text: {text}")
        print(f"{'='*60}")
        print()
        print(f"Server processing time: {server_time}s")
        print(f"Total round-trip time:  {elapsed:.2f}s")
        print(f"Text length:            {len(text)} chars")

        if error:
            print(f"Error:                  {error}")

        return bool(text)

    except requests.exceptions.Timeout:
        elapsed = time.time() - start
        print(f"TIMEOUT after {elapsed:.1f}s")
        print("The endpoint might be cold-starting. Try again in 30 seconds.")
        return False
    except requests.exceptions.ConnectionError as e:
        print(f"CONNECTION ERROR: {e}")
        print("Is the endpoint deployed? Run: modal deploy modal_parakeet/deploy.py")
        return False
    except Exception as e:
        print(f"ERROR: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Test Modal Parakeet endpoint")
    parser.add_argument("audio_file", help="Path to audio file (MP3, WAV, etc.)")
    parser.add_argument("--url", help="Endpoint URL (otherwise reads from .env)")
    args = parser.parse_args()

    url = get_endpoint_url(args.url)
    if not url:
        print("ERROR: No endpoint URL found.")
        print()
        print("Either:")
        print("  1. Set MODAL_ENDPOINT_URL in .env")
        print("  2. Set MODAL_ENDPOINT_URL environment variable")
        print("  3. Pass --url https://your-endpoint.modal.run")
        print()
        print("First deploy the model:")
        print("  pip install modal")
        print("  modal setup")
        print("  modal deploy modal_parakeet/deploy.py")
        sys.exit(1)

    success = test_transcription(args.audio_file, url)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
