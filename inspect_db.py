from app.database import get_db
from app.models import Event, Venue, Assignment
db = next(get_db())
print("Events:")
for e in db.query(Event).all():
    print(f"id={e.id}, name={e.name}, duration_slots={e.duration_slots}")
print("\nAssignments:")
for a in db.query(Assignment).all():
    print(f"event_id={a.event_id}, venue_id={a.venue_id}, slot={a.time_slot}")
