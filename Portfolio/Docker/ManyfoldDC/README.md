Before first run:

Secret key — generate it with:

bash  docker run --rm ghcr.io/manyfold3d/manyfold:latest generate-secret
Paste the output into SECRET_KEY_BASE.

Passwords — DATABASE_PASSWORD and POSTGRES_PASSWORD must be identical.