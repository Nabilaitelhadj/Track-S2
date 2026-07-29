FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends build-essential gfortran libblas-dev liblapack-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /work/trackS
COPY requirements-core.txt /tmp/requirements-core.txt
RUN pip install --no-cache-dir -r /tmp/requirements-core.txt
COPY . /work/trackS
CMD ["bash", "run_center_and_verify.sh"]
