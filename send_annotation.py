#!/usr/bin/env python3
import csv
import os
import sys
import glob
import json
import cytomine
import cytomine.models
import openslide
import pdb
import re
import xml.etree.ElementTree as ET
import shapely.wkt
import numpy as np
import cv2
from shapely.geometry import Polygon, MultiPolygon
from shapely.geometry.base import geom_factory
from shapely.geos import lgeos
from collections import defaultdict
from xml.etree.ElementTree import Element, SubElement, parse

find_contour_returns = cv2.findContours(np.ndarray(
    shape=(100, 100), dtype=np.int32), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_TC89_KCOS)

def get_mpp(slide):
    try:
        mpp_x = float(slide.properties['openslide.mpp-x'])
        mpp_y = float(slide.properties['openslide.mpp-y'])
        return mpp_x, mpp_y
    except:
        try:
            mpp_x = 10000 / float(slide.properties['tiff.XResolution'])
            mpp_y = 10000 / float(slide.properties['tiff.YResolution'])
            return mpp_x, mpp_y
        except:
            raise ValueError("Cannot read mpp")

if len(find_contour_returns) == 3:
    def find_contours(image, mode, method, **kwargs):
        image, contours, hierarchy = cv2.findContours(
            image, mode, method, **kwargs)
        return contours, hierarchy
else:
    def find_contours(image, mode, method, **kwargs):
        return cv2.findContours(image, mode, method, **kwargs)

host = 'http://cytomine-core.annotool.ml'
public_key =  '6cb5c58e-a14a-4b80-bd0f-682db440deeb'
private_key = '3cef1916-b034-4af7-8452-93c9183d4379'

pattern_term_key = {
    'Pattern3': [], 
    'Pattern4': [], 
    'Pattern5': [260926], 
    'IDC-P': []} # 추후 저희가 cytomine에서 정할 term으로 수정 필요합니다.

_class = ['Pattern3', 'Pattern4', 'Pattern5', 'IDC-P']

def convert_to_wkt_coordinate(contour, slide_height, ratio):

    contour = contour * ratio
    contour[:, 1] = slide_height - contour[:, 1]
    return contour
    
def check_clockwise(coor_list):

    coor_list.append(coor_list[0])
    _area = 0
    for i in range(len(coor_list)-1):
        _area+=(coor_list[i][0]*coor_list[i+1][1] - coor_list[i+1][0]*coor_list[i][1])
    if _area > 0:
        return 1
    elif _area < 0:
        return -1
    else:
        return 0

def generate_wkt_from_heatmap(heatmap_dicts, slide_height, _heatmap_to_slide_ratio): # https://michhar.github.io/masks_to_polygons_and_back/ 및 DeepDx Analyzer의 result의 get_xml 참고해서 작성

    output = []
    min_area = 0

    for pattern, heatmap in heatmap_dicts.items():
        heatmap_index = np.ones(heatmap.shape, dtype=np.uint8)
        heatmap_index[heatmap <= 1] = 0
        if heatmap_index.sum() == 0:
            continue
        
        contours, hierarchy = find_contours(
            heatmap_index, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_TC89_KCOS)
        output.append((contours, hierarchy, pattern))

    wkt_list = []
    for pattern_output in output:
        contours, hierarchy, pattern = pattern_output
        if True:#try:
            cnt_children = defaultdict(list)
            child_contours = set()
            assert hierarchy.shape[0] == 1
            
            for idx, (_, _, _, parent_idx) in enumerate(hierarchy[0]):
                if parent_idx != -1:
                    child_contours.add(idx)
                    cnt_children[parent_idx].append(contours[idx])
            for idx, cnt in enumerate(contours):
                if idx not in child_contours and cv2.contourArea(cnt) > min_area:
                    assert cnt.shape[1] == 1
                    poly = Polygon(
                        shell = convert_to_wkt_coordinate(cnt[:, 0, :], slide_height, _heatmap_to_slide_ratio),
                        holes = [convert_to_wkt_coordinate(c[:, 0, :], slide_height, _heatmap_to_slide_ratio) for c in cnt_children.get(idx, [])
                        if cv2.contourArea(c) > min_area])
                        
                    wkt_list.append((poly, pattern))

        #except ValueError:
        #    print(f'No pattern {pattern} found in this slide.')

    return wkt_list

def generate_wkt_from_xml(xml, slide_height): # 모든 contour가 multipolygon이 아니라는 가정 하에 작성된 코드입니다.

    annotation_tree = xml.find('Annotations')
    wkt_list = []
    for ii, anno in enumerate(annotation_tree.findall('Annotation')):
        pattern = anno.attrib['class']
        rotate_list = []
        annotation = []
        for coors in anno.findall('Coordinates'):
            coor_list = []
            for coor in coors.findall('Coordinate'):
                coor_list.append([float(coor.get('x')), float(coor.get('y'))])
            clock = check_clockwise(coor_list)
            if clock == 0:
                continue
            elif clock == 1:
                annotation.append(coor_list)
            elif clock == -1:
                annotation.insert(0, coor_list)
            else:
                print('Error')
            rotate_list.append(clock)
            
        if rotate_list.count(-1) != 1:
            print('Multipolygon Exists!')
            pdb.set_trace()
            continue
        poly = Polygon(
            shell = convert_to_wkt_coordinate(np.array(annotation[0]), slide_height, 1),
            holes = [convert_to_wkt_coordinate(np.array(an), slide_height, 1) for an in annotation[1:]])
        
        wkt_list.append((poly, pattern))
        
    return wkt_list
        
def generate_wkt_list(mask_format, slide_height, _heatmap_to_slide_ratio, xml, heatmap_dicts):

    if mask_format == 'xml':
        wkt_list = generate_wkt_from_xml(xml, slide_height)
    elif mask_format == 'heatmap_dict':
        wkt_list = generate_wkt_from_heatmap(heatmap_dicts, slide_height, _heatmap_to_slide_ratio)
    return wkt_list

def send_to_cytomine(wkt_list, project, map_image):

    with cytomine.Cytomine(host, public_key, private_key) as cobj:
        
        for wkt, pattern in wkt_list:
            _term = pattern_term_key[pattern]
            if wkt.is_valid:
                cytomine.models.Annotation(location=str(wkt), id_image=map_image, id_project=project, id_term=_term).save()
            else:
                cytomine.models.Annotation(location=str(wkt.buffer(0)), id_image=map_image, id_project=project, id_term=_term).save()

def xml_to_heatmap_dict(xml, slide_width, slide_height, _ratio):

    annotation_tree = xml.find('Annotations')
    annotations = []
    patterns = []
    
    mask_x = round(slide_width / _ratio)
    mask_y = round(slide_height / _ratio)    
    
    mask_base = np.zeros((mask_y, mask_x)).astype(np.uint8)
    mask_list = []    

    for i in range(len(_class)):
        mask_list.append(mask_base.copy())
        
    for ii, anno in enumerate(annotation_tree.findall('Annotation')):
        pattern = anno.attrib['class']
        patterns.append(pattern)
        annotation = []
        
        for coords in anno.findall('Coordinates'):
            coor_list = []

            for coor in coords.findall('Coordinate'):
                coor_list.append([round(float(coor.get('x'))/_ratio), \
                                  round(float(coor.get('y'))/_ratio)])

            annotation.append(coor_list)
        annotations.append(annotation)

    for ii, anno in enumerate(annotations):
        pattern = patterns[ii]
        _anno = []
        for coors in anno:
            _anno.append((np.array(coors)).astype(int))
        cv2.drawContours(mask_list[_class.index(pattern)], _anno, -1, 255, -1)

    heatmap_dicts = {}
    
    for i in range(len(_class)):
        heatmap_dicts[_class[i]] = mask_list[i]

    return heatmap_dicts

def get_contours_from_heatmap(heatmap_dicts, slide_size, offset=[0, 0]):
    
    heatmap_size = next(iter(heatmap_dicts.values())).shape
    height, width = heatmap_size
    slide_width, slide_height = slide_size

    res = []
    for class_name, heatmap in heatmap_dicts.items():

        height, width = heatmap.shape
        mask = np.zeros((height, width))

        heatmap_index = np.where(heatmap > 1)
        for i in range(len(heatmap_index[0])):
            mask[heatmap_index[0][i], heatmap_index[1][i]] = 255

        mask = np.uint8(mask)
        contours, hierarchy = cv2.findContours(
            mask,
            cv2.RETR_TREE,
            cv2.CHAIN_APPROX_TC89_KCOS
        )

        c = {}
        for contour_index, contour in enumerate(contours):
            single_contour = [[int(round(coord[0][0]/width * slide_width, 1) - int(offset[0])), int(round(coord[0][1]/height * slide_height, 1) - int(offset[1]))] for i, coord in enumerate(contour) ]
            is_outermost = hierarchy[0][contour_index][3] == -1
            if is_outermost:
                c[contour_index] = [single_contour]
            else:
                parent_idx = contour_index
                while hierarchy[0][parent_idx][3] != -1:
                    parent_idx = hierarchy[0][parent_idx][3]
                c[parent_idx].append(single_contour)
        for path in c.values():
            res.append({
                'contour': path,
                'class': class_name
            })
            
    return res

def generate_wkt_from_openapi(openapi_output, slide_height):

    wkt_list = []
    min_area = 0
    if openapi_output["summary"]["score"] == "Benign":
        return null
    contour_list = openapi_output["heatmap"]["contours"]
    
    for _contours in contour_list:
        contours = _contours['contour']
        pattern = _contours['class']
        rotate_list = []
        annotation = []
        for contour in contours:
            clock = check_clockwise(contour)
            if clock == 0:
                continue
            elif clock == 1:
                annotation.append(contour)
            elif clock == -1:
                annotation.insert(0, contour)
            else:
                print('Error')
            rotate_list.append(clock)
        
        if rotate_list.count(-1) != 1:
            print('Multipolygon Exists!')
            pdb.set_trace()
            continue

        poly = Polygon(
            shell = convert_to_wkt_coordinate(np.array(annotation[0]), slide_height, 1),
            holes = [convert_to_wkt_coordinate(np.array(an), slide_height, 1) for an in annotation[1:]
            if cv2.contourArea(np.array(an)) > min_area])
        
        wkt_list.append((poly, pattern))        
    return wkt_list

if __name__ == "__main__":

    project = 260590
    slide_dir = '/mnt/nfs0/seunghwalee/HE 스캔파일/유방암 Bx/DP-A-BR-B016-1.svs'
    annotation_dir = 'result_origin/DP-A-BR-B016-1.xml'
    map_image = 260774
    
    slide = openslide.OpenSlide(slide_dir)
    slide_width, slide_height = slide.dimensions
    mpp, _ = get_mpp(slide)
    
    with cytomine.Cytomine(host, public_key, private_key) as cobj:
    
        annotations = cytomine.models.AnnotationCollection()
        annotations.image = map_image
        
        annotations.project = project
        annotations.image = map_image
        annotations.showWKT = True
        annotations.showMeta = True
        annotations.showGIS = True
        annotations.showTerm = True
        annotations.fetch()
        
        for old_anno in annotations:
            old_anno.delete()
        
        _heatmap_to_slide_ratio = 0.2465 * 16 / mpp
        
        _xml = parse(annotation_dir)
        heatmap_dicts = xml_to_heatmap_dict(_xml, slide_width, slide_height, _heatmap_to_slide_ratio)
        #wkt_list = generate_wkt_list('heatmap_dict', slide_height, _heatmap_to_slide_ratio, 'None', heatmap_dicts)
        
        
        
        
        res = get_contours_from_heatmap(heatmap_dicts, (slide_width, slide_height))
        openapi_output = {"summary": {"size": 0, "score": "4 + 3"}, "heatmap": {"contours": res}}

        # 나는 여기서 부터 시작하면 됨. 
        wkt_list = generate_wkt_from_openapi(openapi_output, slide_height)
        pdb.set_trace()
        

        
        send_to_cytomine(wkt_list, project, map_image)
        #wkt_list = generate_wkt_list('xml', slide_height, _heatmap_to_slide_ratio, _xml, 'None')
        #send_to_cytomine(wkt_list, project, map_image)
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
