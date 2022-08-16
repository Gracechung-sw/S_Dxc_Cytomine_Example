# xml 에서 heatmap을 읽어 wkt로 변환하는 함수입니다.
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

pattern_term_key = {
    'Pattern3': [260926],
    'Pattern4': [260926],
    'Pattern5': [260926],
    'IDC-P': [260926]}  # 추후 저희가 cytomine에서 정할 term으로 수정 필요합니다.

_class = ['Pattern3', 'Pattern4', 'Pattern5', 'IDC-P']


def convert_to_wkt_coordinate(contour, slide_height, ratio):
    contour = contour * ratio
    contour[:, 1] = slide_height - contour[:, 1]
    return contour


def check_clockwise(coor_list):
    coor_list.append(coor_list[0])
    _area = 0
    for i in range(len(coor_list)-1):
        _area += (coor_list[i][0]*coor_list[i+1][1] -
                  coor_list[i+1][0]*coor_list[i][1])
    if _area > 0:
        return 1
    elif _area < 0:
        return -1
    else:
        return 0

# https://michhar.github.io/masks_to_polygons_and_back/ 및 DeepDx Analyzer의 result의 get_xml 참고해서 작성


def generate_wkt_from_heatmap(heatmap_dicts, slide_height, _heatmap_to_slide_ratio):
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
        if True:  # try:
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
                        shell=convert_to_wkt_coordinate(
                            cnt[:, 0, :], slide_height, _heatmap_to_slide_ratio),
                        holes=[convert_to_wkt_coordinate(c[:, 0, :], slide_height, _heatmap_to_slide_ratio) for c in cnt_children.get(idx, [])
                               if cv2.contourArea(c) > min_area])
                    wkt_list.append((poly, pattern))
        #except ValueError:
        #    print(f'No pattern {pattern} found in this slide.')
    return wkt_list


# 모든 contour가 multipolygon이 아니라는 가정 하에 작성된 코드입니다.
def generate_wkt_from_xml(xml, slide_height):
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
            shell=convert_to_wkt_coordinate(
                np.array(annotation[0]), slide_height, 1),
            holes=[convert_to_wkt_coordinate(np.array(an), slide_height, 1) for an in annotation[1:]])

        wkt_list.append((poly, pattern))

    return wkt_list


def generate_wkt_list(mask_format, slide_height, _heatmap_to_slide_ratio, xml, heatmap_dicts):
    wkt_list = []
    if mask_format == 'xml':
        wkt_list = generate_wkt_from_xml(xml, slide_height)
    elif mask_format == 'heatmap_dict':
        wkt_list = generate_wkt_from_heatmap(
            heatmap_dicts, slide_height, _heatmap_to_slide_ratio)
    return wkt_list


def send_to_cytomine(wkt_list, project, map_image):
    with cytomine.Cytomine(host, public_key, private_key) as cobj:

        for wkt, pattern in wkt_list:
            _term = pattern_term_key[pattern]
            if wkt.is_valid:
                cytomine.models.Annotation(location=str(
                    wkt), id_image=map_image, id_project=project, id_term=_term).save()
            else:
                cytomine.models.Annotation(location=str(wkt.buffer(
                    0)), id_image=map_image, id_project=project, id_term=_term).save()


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
                coor_list.append([round(float(coor.get('x'))/_ratio),
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
