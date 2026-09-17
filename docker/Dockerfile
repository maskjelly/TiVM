FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      at-spi2-core \
      build-essential \
      ca-certificates \
      curl \
      dbus-x11 \
      epiphany-browser \
      fonts-dejavu-core \
      fonts-liberation \
      gir1.2-atspi-2.0 \
      git \
      jq \
      libasound2t64 \
      libdbus-glib-1-2 \
      libglib2.0-bin \
      libssl-dev \
      libx11-xcb1 \
      libxtst6 \
      mousepad \
      novnc \
      openssh-client \
      pkg-config \
      python3 \
      python3-dev \
      python3-gi \
      python3-pip \
      python3-pyatspi \
      python3-venv \
      scrot \
      sudo \
      tesseract-ocr \
      tesseract-ocr-eng \
      thunar \
      unzip \
      wget \
      websockify \
      wmctrl \
      x11-utils \
      x11-xserver-utils \
      x11vnc \
      xdotool \
      xfce4 \
      xfce4-terminal \
      xvfb \
      zip

RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
  && apt-get install -y --no-install-recommends nodejs \
  && npm config set fund false --global

RUN curl -fsSL https://bun.sh/install | bash \
  && mv /root/.bun/bin/bun /usr/local/bin/bun \
  && ln -sf /usr/local/bin/bun /usr/local/bin/bunx

RUN ARCH=$(dpkg --print-architecture) \
  && if [ "$ARCH" = "arm64" ]; then FF=linux64-aarch64; else FF=linux64; fi \
  && curl -fsSL "https://download.mozilla.org/?product=firefox-latest&os=${FF}&lang=en-US" -o /tmp/firefox.tar.xz \
  && mkdir -p /opt/firefox \
  && tar -xJf /tmp/firefox.tar.xz -C /opt/firefox --strip-components=1 \
  && rm /tmp/firefox.tar.xz \
  && ln -s /opt/firefox/firefox /usr/local/bin/firefox \
  && printf '%s\n' \
       '[Desktop Entry]' \
       'Name=Firefox' \
       'Comment=Browse the web' \
       'Exec=/opt/firefox/firefox %u' \
       'Type=Application' \
       'Categories=Network;WebBrowser;' \
       'Icon=/opt/firefox/browser/chrome/icons/default/default128.png' \
       > /usr/share/applications/firefox.desktop

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
