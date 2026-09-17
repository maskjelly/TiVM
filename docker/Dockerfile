FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      at-spi2-core \
      ca-certificates \
      dbus-x11 \
      epiphany-browser \
      fonts-dejavu-core \
      fonts-liberation \
      gir1.2-atspi-2.0 \
      git \
      curl \
      libglib2.0-bin \
      mousepad \
      novnc \
      python3 \
      python3-gi \
      python3-pip \
      python3-pyatspi \
      python3-venv \
      scrot \
      tesseract-ocr \
      tesseract-ocr-eng \
      thunar \
      websockify \
      wmctrl \
      x11-utils \
      x11-xserver-utils \
      x11vnc \
      xdotool \
      xfce4 \
      xfce4-terminal \
      xvfb \
  && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv --system-site-packages /opt/venv \
  && /opt/venv/bin/pip install --no-cache-dir fastapi uvicorn pillow requests

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DISPLAY=:99

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 6080 6081
ENTRYPOINT ["/entrypoint.sh"]
