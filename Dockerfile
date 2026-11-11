FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_DEBUG=false \
    LEMMA_CACHE_DIR=/app/lemma_cache \
    LEMMA_CACHE_BUILD_ALLOWED=false

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY . .

# Build the marisa-trie lemma cache at image build time: building it peaks at
# ~350 MB, which would OOM-kill the 256 MB production machine if it ran
# there instead. --check confirms the build actually produced the file.
RUN DJANGO_SECRET_KEY=build-time-only-not-used-at-runtime-0123456789-abcdefghij DJANGO_DEBUG=false \
    DJANGO_ALLOWED_HOSTS=build.invalid LEMMA_CACHE_BUILD_ALLOWED=true \
    python manage.py build_lemma_cache
RUN DJANGO_SECRET_KEY=build-time-only-not-used-at-runtime-0123456789-abcdefghij DJANGO_DEBUG=false \
    DJANGO_ALLOWED_HOSTS=build.invalid python manage.py build_lemma_cache --check

# collectstatic runs at build time so the manifest static storage is baked into the image.
# SECRET_KEY is not needed for collectstatic, but settings import requires it be set.
RUN DJANGO_SECRET_KEY=build-time-only-not-used-at-runtime-0123456789-abcdefghij DJANGO_DEBUG=false \
    DJANGO_ALLOWED_HOSTS=build.invalid python manage.py collectstatic --noinput

RUN chmod +x scripts/docker-entrypoint.sh

EXPOSE 8080
CMD ["scripts/docker-entrypoint.sh"]
