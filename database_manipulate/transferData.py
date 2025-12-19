import sqlite3

engine = sqlite3.connect('../yaps.db')

c = engine.cursor()

c.execute("ATTACH 'databacy.db' AS other")

# c.execute("CREATE TABLE task AS SELECT * FROM other.transcripted")

# c.execute("CREATE TABLE user AS SELECT * FROM other.user")

c.execute("INSERT INTO task SELECT * FROM other.transcripted")

engine.commit()
