from flask import Flask
import logging

app = Flask(__name__)

# Disable werkzeug logs
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

@app.route('/healthz')
def health_check():
    return 'OK', 200

def run():
    app.run(host='0.0.0.0', port=8080)

if __name__ == '__main__':
    run()