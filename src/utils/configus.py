#!/usr/bin/python
#configus.py
# -*- coding: utf-8 -*-

'''
Created on 2018. 11. 15.

@author: miskang

'''
import os
import json

def load_config(filename):
    config_value={}
    if not os.path.isfile(filename):
        #make json file if not exist
        print(f'{filename} is not exist')
    with open(filename, 'r', encoding='utf-8') as data_file:
        config_value = json.load(data_file)
    return config_value

def save_config(json_dict, filename):
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(json_dict, f, ensure_ascii=False, indent=4)
    return json_dict