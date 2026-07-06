source ../.venv/bin/activate
# Analyzing Assistant and puller is in internal netwrok, so these are accessible under no_proxy envrionment. Need to add in no_proxy env vairable
export no_proxy="127.0.0.1, 12.81.220.16, $no_proxy"
export |grep proxy
uvicorn main:app --reload --port 8000 --host 0.0.0.0
