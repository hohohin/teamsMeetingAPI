import sqlite3


BASE_NAME = 'yaps.db'


engine = sqlite3.connect(BASE_NAME)

c = engine.cursor()

c.execute("DELETE FROM task WHERE status='NONE'")
c.execute("DELETE FROM task WHERE status='FAILED'")
c.execute("DELETE FROM user WHERE username='未设置用户名'")

c.execute("INSERT INTO user () VALUE")


engine.commit()