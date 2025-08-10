import redis

r = redis.Redis(host='localhost', port=6379, db=0)

r.set('lunessa:status', 'connected and running')
print("Set 'lunessa:status' to 'connected and running'")

status = r.get('lunessa:status')
print(f"Retrieved status: {status.decode('utf-8')}")