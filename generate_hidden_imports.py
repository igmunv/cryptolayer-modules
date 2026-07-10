# скрипт запускается перед сборкой или запуском основного проекта.
# нужен для того чтобы все зависимости модулей были установлены.
# после скрипта в главном файле приложения необходимо импортировать hidden_imports.py
#
# в приложении необходимо импортировать файл hidden_imports.py, который сгенерируется в текущей директории
#

import os

def generate_hid_imports():

    root_dir = os.path.dirname(os.path.abspath(__file__))

    # собираем в сет все зависимости каждого модуля
    module_reqs = set()
    for folder in os.listdir(root_dir):
        req_file = os.path.join(root_dir, folder, "requirements.txt")
        if os.path.exists(req_file):
            with open(req_file, "r") as f:
                module_reqs.update(line.strip() for line in f if line.strip())

    hidden_imports = set()
    for mod in module_reqs:
        pkg = mod.strip().split('==')[0].split('>=')[0]
        hidden_imports.add(pkg)

    with open(os.path.join(root_dir, "hidden_imports.py"), "w") as f:
        f.write("# Автоматически сгенерировано для PyInstaller\n")
        for imp in sorted(hidden_imports):
            module_name = imp.replace("-", "_")
            f.write(f"try:\n    import {module_name}\nexcept ImportError:\n    pass\n")


if __name__ == "__main__":
    generate_hid_imports()


