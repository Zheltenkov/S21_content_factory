import subprocess
import os
import sys

PATH_TO_PROJECT = '/'.join(os.path.abspath(os.path.dirname(sys.argv[0])).split('/')[:-1])

test_name = sys.argv[1]
devnull_stderr = subprocess.DEVNULL
devnull_stdout = subprocess.DEVNULL


def get_source_filenames(arr_of_files=None, depth=0, path=PATH_TO_PROJECT + '/src'):
    if arr_of_files is None:
        arr_of_files = []

    if depth == 3:
        return

    for file in os.listdir(path):
        new_path = os.path.join(path, file)
        if os.path.isdir(new_path):
            get_source_filenames(arr_of_files, depth + 1, new_path)
        elif file[file.find('.') + 1 : len(file)] == 'c':
            arr_of_files.append(new_path)

    return arr_of_files


def cppcheck_test_result(arr_of_files):
    if arr_of_files == []:
        return False
        
    result_cppcheck_test = subprocess.run(
        ['cppcheck', '-q', '--enable=all',
         '--language=c', '--suppress=missingIncludeSystem',
         '--suppress=checkersReport', '--check-level=exhaustive',
        *arr_of_files],
        stderr=devnull_stderr, stdout=subprocess.PIPE, text=True)
    if len(result_cppcheck_test.stdout) != 0:
        return False

    return True


arr_of_files = get_source_filenames()
if cppcheck_test_result(arr_of_files):
    print('Static code analysis: OK\n 1')
else:
    print('Static code analysis: FAIL\n 0')
