item = str([123, 543, 876])

a = item.lstrip('[').rstrip(']').replace(" ", "")
print(a)