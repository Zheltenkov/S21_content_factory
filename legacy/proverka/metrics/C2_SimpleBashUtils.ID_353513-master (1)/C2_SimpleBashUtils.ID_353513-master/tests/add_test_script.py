import json
import os

PATH_TO_ROOT = os.path.dirname(os.path.abspath(__file__))
TEST_JSON_NAME = 'tests.json'


def create_test(quest_number):
    last_test = 0
    if quest_number > 1:
        for root, dirs, files in os.walk(PATH_TO_ROOT + '\\Quest_' + str(quest_number - 1)):
            for file in files:
                if file[0].isdigit() and int(file[0:file.index('.')]) > last_test:
                    last_test = int(file[0:file.index('.')])
    for root, dirs, files in os.walk(PATH_TO_ROOT + '\\Quest_' + str(quest_number)):
        for file in files:
            if file[0].isdigit() and int(file[0:file.index('.')]) > last_test:
                last_test = int(file[0:file.index('.')])
    f_in = open(PATH_TO_ROOT + '\\Quest_' + str(quest_number) + '\\' + str(last_test + 1) + '.in', 'w')
    f_out = open(PATH_TO_ROOT + '\\Quest_' + str(quest_number) + '\\' + str(last_test + 1) + '.out', 'w')
    f_args = open(PATH_TO_ROOT + '\\Quest_' + str(quest_number) + '\\' + str(last_test + 1) + '.args', 'w')
    f_file = open(PATH_TO_ROOT + '\\Quest_' + str(quest_number) + '\\' + str(last_test + 1) + '.file', 'w')
    f_in.close()
    f_out.close()
    f_args.close()
    f_file.close()
    rename_tests_start_with_quest(quest_number, 1)
    create_new_json_with_add_test(quest_number, last_test)


def create_new_json_with_delete_test(number_deleted_test):
    with open(TEST_JSON_NAME, 'r', encoding='utf-8') as json_file:
        json_tests = json.load(json_file)

    new_json_tests = {'tests': []}
    for test in json_tests['tests']:
        if test['number'] > number_deleted_test:
            test['number'] -= 1
            new_json_tests['tests'].append(test)
        elif test['number'] < number_deleted_test:
            new_json_tests['tests'].append(test)

    with open(TEST_JSON_NAME, 'w') as write_file:
        json.dump(new_json_tests, write_file, indent=4)


def create_new_json_with_add_test(quest_number, last_test_without_rename, category='basic'):
    with open(TEST_JSON_NAME, 'r', encoding='utf-8') as json_file:
        json_tests = json.load(json_file)

    for test in json_tests['tests']:
        if test['number'] > last_test_without_rename:
            test['number'] += 1

    new_test = {'number': last_test_without_rename + 1,
                'name': 'Quest_' + str(quest_number),
                'category': category,
                'result': 'PLACEHOLDER'}
    json_tests['tests'].append(new_test)

    json_tests['tests'] = sorted(json_tests['tests'], key=lambda x: x['number'])

    with open(TEST_JSON_NAME, 'w') as write_file:
        json.dump(json_tests, write_file, indent=4)


# Renames all test files in the directory to the change_test_number coefficient, starting with the from_test test
def file_renaming(dir, change_test_number, from_test):  # from_test == -1 - rename all files
    for root, dirs, files in os.walk(PATH_TO_ROOT + '\\' + dir):
        new_filenames = []
        for file in files:
            if file[0].isdigit() and (int(file[:file.index('.')]) >= from_test or from_test == -1):
                new_name = 'torename' + file[file.index('.') + 1:] + '_' + file[0:file.index('.')]
                os.rename(PATH_TO_ROOT + '\\' + dir + '\\' + file,
                          PATH_TO_ROOT + '\\' + dir + '\\' + new_name)
                new_filenames.append(new_name)
        for file in new_filenames:
            if 'torenamein' in file:
                new_name = str(int(file[file.index('_') + 1:]) + change_test_number) + '.in'
                os.rename(PATH_TO_ROOT + '\\' + dir + '\\' + file,
                          PATH_TO_ROOT + '\\' + dir + '\\' + new_name)
            elif 'torenameout' in file:
                new_name = str(int(file[file.index('_') + 1:]) + change_test_number) + '.out'
                os.rename(PATH_TO_ROOT + '\\' + dir + '\\' + file,
                          PATH_TO_ROOT + '\\' + dir + '\\' + new_name)
            elif 'torenameargs' in file:
                new_name = str(int(file[file.index('_') + 1:]) + change_test_number) + '.args'
                os.rename(PATH_TO_ROOT + '\\' + dir + '\\' + file,
                          PATH_TO_ROOT + '\\' + dir + '\\' + new_name)
            elif 'torenamefile' in file:
                new_name = str(int(file[file.index('_') + 1:]) + change_test_number) + '.file'
                os.rename(PATH_TO_ROOT + '\\' + dir + '\\' + file,
                          PATH_TO_ROOT + '\\' + dir + '\\' + new_name)


# renames all tests in quests, starting with "Quest_<quest_number+1>"
def rename_tests_start_with_quest(quest_number, change_test_number):
    for root, dirs, files in os.walk(PATH_TO_ROOT):
        for dir in dirs:
            if '_' in dir and int(dir[dir.index('_') + 1:]) > quest_number:
                file_renaming(dir, change_test_number, -1)


# renames all subsequent tests after the deleted test
def rename_tests_with_delete(from_test_number):  # from_test_number - number of the first renaming test
    quest_with_test = -1
    for root, dirs, files in os.walk(PATH_TO_ROOT):
        for dir in dirs:
            for root, dirs, files in os.walk(PATH_TO_ROOT + '\\' + dir):
                for file in files:
                    if file[0].isdigit() and int(file[0:file.index('.')]) == from_test_number:
                        quest_with_test = int(dir[dir.index('_') + 1:])
                        break
                if quest_with_test != -1:
                    break
            if quest_with_test != -1:
                break
        if quest_with_test != -1:
            break
    if quest_with_test != -1:
        file_renaming('Quest_' + str(quest_with_test), -1, from_test_number)
        rename_tests_start_with_quest(quest_with_test, -1)


# The function deletes the desired test and renames all subsequent tests to maintain end-to-end numbering.
def delete_test(test_to_delete):
    for root, dirs, files in os.walk(PATH_TO_ROOT):
        for dir in dirs:
            for root, dirs, files in os.walk(PATH_TO_ROOT + '\\' + dir):
                for file in files:
                    if file[0].isdigit() and int(file[0:file.index('.')]) == test_to_delete:
                        os.remove(PATH_TO_ROOT + '\\' + dir + '\\' + file)
    rename_tests_with_delete(test_to_delete + 1)
    create_new_json_with_delete_test(test_to_delete)


if __name__ == '__main__':
    what_to_do = int(input('Чтобы удалить тест, введите 1, чтобы добавить - 2: '))
    if what_to_do == 1:
        test_to_delete = int(input('Номер удаляемого теста: '))
        delete_test(test_to_delete)
    elif what_to_do == 2:
        add_test = int(input('В какой квест добавить тест: '))
        create_test(add_test)
