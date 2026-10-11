FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 \
      libglu1-mesa \
      libxrender1 \
      libxext6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml requirements.lock ./
COPY src ./src
COPY config ./config
COPY tests ./tests
COPY conftest.py ./
RUN python -m pip install --no-cache-dir -r requirements.lock \
    && python -m pip install --no-cache-dir --no-deps .

# Mount local inputs and outputs at runtime. Do not bake the licensed STEP asset
# or generated geometry into the image.
ENTRYPOINT ["python", "-m", "shoe_poc"]
