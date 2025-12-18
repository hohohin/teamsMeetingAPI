import sqlite3

# ---- create sheet

# 1. 连接到数据库
# 如果文件不存在，它会自动在当前目录下创建 'example.db'
conn = sqlite3.connect('students.db')

# 2. 创建一个游标对象
c = conn.cursor()



# 3.执行 SQL 创建表
# 我们用三引号 ''' 来写多行字符串，这样 SQL 看起来更清晰
try:
    c.execute('''
        CREATE TABLE students (
            id TEXT,
            name TEXT,
            score INTEGER
        )
    ''')

# Python 类型,SQLite 类型,说明
# str,TEXT,文本字符串 (学号、姓名)
# int,INTEGER,整数 (分数)
# float,REAL,小数   
    
    conn.commit()
except sqlite3.OperationalError as e:
    print('table already exsist.')

    # 4. 提交事务（保存更改）
    c.execute("INSERT INTO students (id, name, score) VALUES ('A001', 'admin', 100)")
    conn.commit()



