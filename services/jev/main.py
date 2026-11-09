from common.http import private_app
from jev import rank_local

app = private_app('jev')


@app.post('/rank')
def rank(body: dict):
    return rank_local(body['tasks'], body['now'])
