with open('app/static/style.css', 'r', encoding='utf-8') as f:
    content = f.read()
opens = content.count('{')
closes = content.count('}')
print(f"Opens: {opens}, Closes: {closes}")
if opens != closes:
    print(f"Difference: {opens - closes}")
else:
    print("CSS braces are perfectly balanced!")
