#!/usr/bin/python
# config.py
# -*- coding: utf-8 -*-

'''
Created on 2018. 11. 15.
modified on 2024. 08. 10.

@author: miskang
'''
import os
import json
import inspect


def load_config(filename):
    config_value = {}

    if not os.path.isfile(filename):
        # 호출한(Callers) 함수 및 파일 정보 추적
        frame = inspect.currentframe().f_back  # 나를 호출한 상위 스택 프레임
        caller_file = os.path.basename(frame.f_code.co_filename)  # 호출한 파일명
        caller_func = frame.f_code.co_name  # 호출한 함수명
        caller_line = frame.f_lineno  # 호출한 줄 번호

        print(
            f"[Config Error] '{filename}' 파일이 존재하지 않습니다. "
            f"(Called from '{caller_file}' -> {caller_func}() at line {caller_line})"
        )
        return config_value  # 파일이 없을 때는 빈 객체(또는 None)를 반환하고 즉시 종료

    with open(filename, 'r', encoding='utf-8') as data_file:
        config_value = json.load(data_file)
    return config_value


def save_config(json_dict, filename):
    with open(filename, 'w', encoding='utf-8') as jsonFile:
        json.dump(json_dict, jsonFile, ensure_ascii=False, indent='\t')
    return json_dict