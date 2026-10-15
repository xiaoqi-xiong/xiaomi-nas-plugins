#!/usr/bin/env python3
"""CGI entrypoint for the owner-scoped Songloft Music gateway."""

from music import serve


if __name__ == "__main__":
    serve()
