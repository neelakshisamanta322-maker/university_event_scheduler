import urllib.request
resp = urllib.request.urlopen('http://127.0.0.1:8000/static/style.css?v=8.0')
css = resp.read().decode('utf-8')
print("Status:", resp.status)
print("Light navy rule:", '#172554' in css)
print("Emerald rule:", '#064E3B' in css)
print("CSS length:", len(css))
