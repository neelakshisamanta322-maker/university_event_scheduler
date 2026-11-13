from app.demo import load_demo
from app.scheduler import solve_schedule
data = load_demo()
res = solve_schedule(data)
print("Success:", res.get("success"))
for a in res.get("assignments", []):
    print(f"event_id={a.get('event_id')}, name={a.get('event_name')}, venue={a.get('venue_name')}, slots={a.get('slots')}")
