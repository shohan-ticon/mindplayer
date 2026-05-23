class Divide:
    def __init__(self, a, b):
        self.a = a
        self.b = b

    def compute(self):
        if self.b == 0:
            raise ZeroDivisionError("cannot divide by zero")
        result = self.a / self.b
        return result
