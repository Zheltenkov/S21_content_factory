import os
import subprocess
import sys

PATH_TO_PROJECT = '/'.join(os.path.abspath(os.path.dirname(sys.argv[0])).split('/')[:-1])

devnull_stderr = sys.stdout
devnull_stdout = sys.stdout

test_name = sys.argv[1]


def get_all_about_make():
    file = open(PATH_TO_PROJECT + '/tests/' + test_name + '/compile_file')
    all_about_make = file.read().split()
    file.close()
    return all_about_make


def build(makefile_dir, makefile_name, makefile_stage):
    try:
        build_result = subprocess.run(['make', makefile_stage],
                                      stderr=devnull_stderr, stdout=devnull_stdout, text=True,
                                      cwd=PATH_TO_PROJECT + '/' + makefile_dir)
    except Exception as e:
        return -1

    return build_result.returncode


def run():
    makefile_dir, makefile_name, makefile_stage = get_all_about_make()
    result_build_code = build(makefile_dir, makefile_name, makefile_stage)

    if result_build_code != 0:
        print('\n\nProject build: FAIL\n0')
    else:
        print('\n\nProject build: OK\n1')


run()
