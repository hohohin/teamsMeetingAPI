import sqlite3


BASE_NAME = 'yaps.db'


engine = sqlite3.connect(BASE_NAME)

c = engine.cursor()

c.execute("DELETE FROM task WHERE status='NONE'")

engine.commit()