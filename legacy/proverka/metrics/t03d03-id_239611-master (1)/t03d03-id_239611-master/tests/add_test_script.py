import json
import os

PATH_TO_ROOT = os.path.dirname(os.path.abspath(__file__))
TEST_JSON_NAME = 'tests.json'


def create_test(quest_number):
    last_test = 0
    for root, dirs, files in os.walk(PATH_TO_ROOT + '\\Quest_' + str(quest_number)):
        for file in files:
            if file[0].isdigit() and int(file[0:file.index('.')]) > last_test:
                last_test = int(file[0:file.index('.')])
    f_in = open(PATH_TO_ROOT + '\\Quest_' + str(quest_number) + '\\' + str(last_test + 1) + '.in', 'w')
    f_out = open(PATH_TO_ROOT + '\\Quest_' + str(quest_number) + '\\' + str(last_test + 1) + '.out', 'w')
    f_in.close()
    f_out.close()
    rename_tests_start_with_quest(quest_number)
    create_new_json(quest_number, last_test)


def create_new_json(quest_number, last_test, category='basic'):
    with open(TEST_JSON_NAME, "r", encoding='utf-8') as json_file:
        json_tests = json.load(json_file)

    for test in json_tests['tests']:
        if test['number'] > last_test:
            test['number'] += 1

    new_test = {'number': last_test + 1,
                'name': 'Quest_' + str(quest_number),
                'category': category,
                'result': 'PLACEHOLDER'}
    json_tests['tests'].append(new_test)

    json_tests['tests'] = sorted(json_tests['tests'], key=lambda x: x['number'])

    with open(TEST_JSON_NAME, "w") as write_file:
        json.dump(json_tests, write_file, indent=4)


def rename_tests_start_with_quest(quest_number):
    for root, dirs, files in os.walk(PATH_TO_ROOT):
        for dir in dirs:
            if int(dir[dir.index('_') + 1:]) > quest_number:
                for root, dirs, files in os.walk(PATH_TO_ROOT + '\\' + dir):
                    new_filenames = []
                    for file in files:
                        if file[0].isdigit():
                            new_name = 'torename' + file[file.index('.') + 1:] + '_' + file[0:file.index('.')]
                            os.rename(PATH_TO_ROOT + '\\' + dir + '\\' + file,
                                      PATH_TO_ROOT + '\\' + dir + '\\' + new_name)
                            new_filenames.append(new_name)
                    for file in new_filenames:
                        if 'torenamein' in file:
                            new_name = str(int(file[file.index('_') + 1:]) + 1) + '.in'
                            os.rename(PATH_TO_ROOT + '\\' + dir + '\\' + file,
                                      PATH_TO_ROOT + '\\' + dir + '\\' + new_name)
                        elif 'torenameout' in file:
                            new_name = str(int(file[file.index('_') + 1:]) + 1) + '.out'
                            os.rename(PATH_TO_ROOT + '\\' + dir + '\\' + file,
                                      PATH_TO_ROOT + '\\' + dir + '\\' + new_name)


if __name__ == '__main__':
    add_test = int(input('В какой квест добавить тест: '))
    create_test(add_test)
