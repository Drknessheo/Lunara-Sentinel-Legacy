import redis

r = redis.Redis(host='localhost', port=6379, db=0)

r.set('lunessasignals:status', 'connected and running')
print("Set 'lunessasignals:status' to 'connected and running'")

status = r.get('lunessasignals:status')
print(f"Retrieved status: {status.decode('utf-8')}")