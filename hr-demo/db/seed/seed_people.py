"""Founding organization and employee identities; the caller owns the random stream."""
import datetime as dt

DEPTS = [
    ("技术部", "北京", 1.15), ("产品部", "北京", 1.05), ("运营部", "北京", 1.00),
    ("销售部", "上海", 0.90), ("市场部", "上海", 0.95), ("客服部", "成都", 0.80),
    ("人事部", "北京", 0.90), ("财务部", "北京", 0.90), ("总经办", "北京", 1.20),
]
DEPT_W = [("技术部", 34), ("产品部", 8), ("运营部", 10), ("销售部", 16),
          ("市场部", 7), ("客服部", 12), ("人事部", 5), ("财务部", 4)]
TITLES = {
    "技术部": ["软件工程师", "高级软件工程师", "测试工程师", "运维工程师", "数据工程师", "算法工程师", "架构师"],
    "产品部": ["产品经理", "高级产品经理", "产品运营", "UI设计师"],
    "运营部": ["运营专员", "用户运营", "数据分析师", "活动策划"],
    "销售部": ["销售代表", "大客户经理", "销售运营", "渠道经理"],
    "市场部": ["市场专员", "品牌经理", "内容运营", "市场分析师"],
    "客服部": ["客服代表", "客服主管", "质检专员"],
    "人事部": ["HR专员", "HRBP", "招聘专员", "培训专员", "薪酬绩效专员"],
    "财务部": ["会计", "财务分析师", "出纳", "税务专员"],
    "总经办": ["总经理助理"],
}
LEVEL_SALARY = {"初级": (9, 14), "中级": (15, 22), "高级": (23, 32),
                "专家": (33, 48), "总监": (45, 65), "副总裁": (70, 90)}
GENDER_W = [("男", 55), ("女", 45)]
SOURCE_W = [("内推", 25), ("招聘网站", 45), ("猎头", 12), ("校园招聘", 18)]
CITY_MULT = {"北京": 1.05, "上海": 1.00, "深圳": 0.95, "杭州": 0.90, "成都": 0.75, "远程": 0.85}
CITIES = list(CITY_MULT)
SURNAME = "王李张刘陈杨黄赵吴周徐孙马朱胡郭何林罗高郑梁谢宋唐许韩冯邓曹彭曾肖田董潘袁蔡蒋余杜叶程魏苏吕"
MALE_GIVEN = ["伟", "强", "磊", "军", "洋", "勇", "杰", "涛", "斌", "波", "辉", "刚", "健", "明", "俊", "帆", "宇", "浩", "凯", "晨", "子轩", "浩然", "俊杰", "志强", "建国", "建华", "晓东", "文博", "天宇", "思远"]
FEMALE_GIVEN = ["芳", "娟", "敏", "静", "丽", "娜", "艳", "琳", "雪", "慧", "颖", "婷", "玉", "莹", "雪莲", "雨欣", "梦琪", "欣怡", "晓燕", "海燕", "佳怡", "思琪", "晓雯", "雅静", "诗涵"]
EDUCATION = {
    "初级": [("大专", 12), ("本科", 62), ("硕士", 22), ("博士", 4)],
    "中级": [("大专", 8), ("本科", 60), ("硕士", 28), ("博士", 4)],
    "高级": [("本科", 50), ("硕士", 40), ("博士", 10)],
    "专家": [("本科", 50), ("硕士", 40), ("博士", 10)],
    "总监": [("本科", 25), ("硕士", 60), ("博士", 15)],
    "副总裁": [("本科", 25), ("硕士", 60), ("博士", 15)],
}


class People:
    def __init__(self, rng):
        self.rng = rng
        self.dept_id = {name: i for i, (name, _, _) in enumerate(DEPTS, 1)}
        self.id_dept = {i: name for name, i in self.dept_id.items()}
        self.locations = {name: city for name, city, _ in DEPTS}
        self.multipliers = {name: factor for name, _, factor in DEPTS}
        self.departments = [{"dept_id": i, "dept_name": name, "parent_id": None, "location": city,
                             "established_date": dt.date(2020, 6, 1)}
                            for i, (name, city, _) in enumerate(DEPTS, 1)]
        self.employees, self.seen_names, self.directors = [], set(), {}
        self.ceo = self.add(dt.date(2020, 6, 1), "总经办", "总经理", "副总裁",
                            gender="男", age=42, etype="全职", edu="硕士")
        for dept, _, _ in DEPTS:
            if dept == "总经办":
                continue
            directors = []
            for _ in range(2 if dept == "技术部" else 1):
                title = "技术总监" if dept == "技术部" else f"{dept.replace('部', '')}总监"
                employee = self.add(dt.date(2020, 6, 1) + dt.timedelta(days=rng.randint(0, 300)),
                                    dept, title, "总监", etype="全职")
                employee["manager_id"] = self.ceo["emp_id"]
                directors.append(employee)
            self.directors[dept] = directors
        for _ in range(272):
            dept = rng.weighted(DEPT_W)
            level = rng.weighted([("初级", 40), ("中级", 35), ("高级", 18), ("专家", 7)])
            title = rng.choice(TITLES[dept])
            hire = rng.workday(dt.date(2020, 6, 1), dt.date(2022, 12, 31))
            employee = self.add(hire, dept, title, level)
            employee["manager_id"] = rng.choice(self.directors[dept])["emp_id"]

    def name(self, seen, gender):
        while True:
            name = self.rng.choice(SURNAME) + self.rng.choice(MALE_GIVEN if gender == "男" else FEMALE_GIVEN)
            if len(name) >= 2 and name not in seen:
                seen.add(name)
                return name

    def salary(self, level, dept, city):
        low, high = LEVEL_SALARY[level]
        value = self.rng.uniform(low, high) * self.multipliers[dept] * CITY_MULT.get(city, 1.0) * 1000
        return float(max(5000, round(value / 100) * 100))

    def department(self, employee):
        return self.id_dept[employee["dept_id"]]

    def add(self, hire_date, dept, title, level, city=None, etype=None, gender=None, age=None, salary=None, edu=None):
        rng = self.rng
        eid = len(self.employees) + 1
        gender = gender or rng.weighted(GENDER_W)
        if age is None:
            age = (rng.randint(33, 48) if level in ("总监", "副总裁") else rng.weighted([
                (rng.randint(22, 28), 45), (rng.randint(29, 35), 35), (rng.randint(36, 45), 20)]))
        city = city or rng.weighted([(self.locations[dept], 60), (rng.choice(CITIES), 40)])
        etype = etype or rng.weighted([("全职", 82), ("外包", 8), ("实习", 6), ("兼职", 4)])
        employee = {
            "emp_id": eid, "emp_no": f"EMP{eid:04d}", "name": self.name(self.seen_names, gender),
            "gender": gender, "birth_date": dt.date(hire_date.year - age, hire_date.month, hire_date.day),
            "hire_date": hire_date, "dept_id": self.dept_id[dept], "job_title": title, "job_level": level,
            "employment_type": etype, "status": "在职",
            "base_salary": salary if salary is not None else self.salary(level, dept, city),
            "manager_id": None, "work_city": city, "email": f"emp{eid:04d}@xingchen-tech.com",
            "phone": "1" + rng.choice("3589") + "".join(rng.choices("0123456789", k=8)),
            "education": edu or rng.weighted(EDUCATION[level]),
            "termination_date": None, "termination_reason": None, "is_voluntary": None,
        }
        self.employees.append(employee)
        return employee
