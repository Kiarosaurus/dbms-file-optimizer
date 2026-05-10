FROM python:3.11-slim

# Tkinter + X11 libs para la GUI
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-tk \
        libx11-6 \
        libxext6 \
        libxrender1 \
        libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Crea dirs de runtime para que no fallen en primer arranque
RUN mkdir -p workspaces/default_testing results docs/charts

CMD ["python", "run_gui.py"]
