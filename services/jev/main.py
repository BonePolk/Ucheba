from common.http import private_app
from jev import rank_local
from common.progress import Progress

app = private_app('jev')


@app.post('/rank')
def rank(body: dict):
    with Progress('Jev', body.get('task_id', 0)) as progress:
        progress.update(stage='waiting', completed=0, total=0, comparisons=0)
        return rank_local(body['tasks'], body['now'], progress)
