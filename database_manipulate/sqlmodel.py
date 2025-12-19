from sqlmodel import SQLModel, Field, create_engine, Session, select
from typing import Optional

# 1. 继承 SQLModel
# 2. table=True 表示这是一个数据库表
class Student(SQLModel, table=True):
    # Field(primary_key=True) 表示这是主键
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    score: Optional[int] = None


# 连接数据库 即engine = sqlite3.connect('students.db')
# 一个统一的engine（校门口）
engine = create_engine('sqlite:///students.db')

class StudentManager:
    def __init__(self, engine) -> None:
        self.engine = engine

    def init_db(self):
        SQLModel.metadata.create_all(self.engine)

        # SQLModel.metadata.create_all(engine)
        # 这里的 create_all 会扫描所有 table=True 的模型，并在数据库中创建它们
        
    def add_student(self, student: Student):
        # 使用 with 语句自动管理会话
        with Session(self.engine) as session:
            # 1. 把 student1 加入到会话（购物车）中
            session.add(student)
            # 2. 提交会话，把数据写入数据库（结账）
            session.commit()
    # student1 = Student(name='Amy',score=95)
    # 这一步非常关键的概念： 现在，"Amy" 还只是漂浮在 Python 内存里的一个对象。
    # 如果你这时候去查看 students.db 数据库文件，你会发现里面还是空的。
    # 我们需要把这个 Python 对象“推”进数据库里。在 SQLModel（以及 SQLAlchemy）中，我们需要使用 会话 (Session) 来管理这种交互。


    def read_student_by_name(self, name):
        # 1. 构造查询语句 (仅仅是生成语句，还没执行)
        # 翻译：从 Hero 表中选择数据，条件是 name 等于 "Spider-Boy"
        statement = select(Student).where(Student.name == name)
        # 2. 执行查询
        # session.exec() 负责把语句发给数据库，.first() 表示只拿回找到的第一个结果
        with Session(self.engine) as session:
            student = session.exec(statement).first()
            return student

    # 这里的student是从查方法里面找到的student对象
    def update_db(self, student: Student):
        with Session(self.engine) as session:
            session.add(student)
            session.commit()

    def delete_db(self, student: Student):
        with Session(self.engine) as session:
            current_student = session.get(Student, student.id)
            if current_student:
                session.delete(current_student)
                session.commit()