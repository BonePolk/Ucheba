from common.http import private_app
from jev import evaluate_local

app = private_app('jev')


@app.post('/evaluate')
def evaluate(body: dict):
    return evaluate_local(body['state'])
