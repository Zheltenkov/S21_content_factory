import os

PATH_TO_ROOT = os.path.dirname(os.path.abspath(__file__))

def add_args_file():
    for root, dirs, files in os.walk(PATH_TO_ROOT):
        for dir in dirs:
            if "Quest_" in dir:
                for root, dirs, files in os.walk(PATH_TO_ROOT + '\\' + dir):
                    for file in files:
                        if file[0].isdigit():
                            f_args = open(dir + '\\' + file[0:file.index('.')] + '.args', 'w')
                            f_args.close()


if __name__ == '__main__':
    add_args_file()