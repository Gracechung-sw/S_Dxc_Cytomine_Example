import os
import sys
import logging
import shutil
import time
import json
import uuid

import openslide
from shapely.geometry import Point, box
import pandas as pd

import cytomine
from cytomine.models import ImageInstance, ImageInstanceCollection, Job, JobData, Property, Annotation, AnnotationTerm, AnnotationCollection
from api import get_upload_url, start_analysis, upload_file, get_analysis_status, get_analysis_result
from contours import get_mpp, convert_to_wkt_coordinate, check_clockwise, generate_wkt_from_heatmap, generate_wkt_list, send_to_cytomine, xml_to_heatmap_dict

SUCCESS_STATUS = ["FINISHED"]
FAILED_STATUS = ["DOWNLOAD_FAILED", "FAILED"]
pattern_term_key = {
    'Pattern3': [],
    'Pattern4': [],
    'Pattern5': [260926],
    'IDC-P': []}  # TODO: 추후 저희가 cytomine에서 정할 term으로 수정 필요합니다.
    

_class = ['Pattern3', 'Pattern4', 'Pattern5', 'IDC-P']


def parse_domain_list(s):
    if s is None or len(s) == 0:
        return []
    return list(map(int, s.split(',')))


def run(cyto_job, parameters):
    logging.info("Entering run(cyto_job=%s, parameters=%s)",
                 cyto_job, parameters)

    job = cyto_job.job
    project = cyto_job.project
    project_id = project.id
    # I create a working directory that I will delete at the end of this run
    working_path = os.path.join("tmp", str(job.id))
    img_download_folder = os.path.join("img", str(project_id))
    if not os.path.exists(working_path):
        logging.info("Creating working directory: %s", working_path)
        os.makedirs(working_path)
    if not os.path.exists(img_download_folder):
        logging.info("Creating img download directory: %s",
                     img_download_folder)
        os.makedirs(img_download_folder)

    try:
        ai_model_parameter = parameters.ai_model_type
        images_to_analyze = parameters.cytomine_id_images

        logging.info("Display ai_model_parameter %s", ai_model_parameter)

        # -- Select images to process or Get list of images in my project
        images = ImageInstanceCollection()
        if images_to_analyze is not None:
            images_id = parse_domain_list(images_to_analyze)
            images.extend([ImageInstance().fetch(_id) for _id in images_id])
        else:
            images = images.fetch_with_filter("project", project_id)
        nb_images = len(images)
        logging.info("# images in project: %d", nb_images)

        # value between 0 and 100 that represent the progress bar displayed in the UI.
        progress = 0
        progress_delta = 100 / nb_images

        for (i, image) in enumerate(images):
            image_filie_name = image.instanceFilename
            image_id = image.id

            # -- ai analysis request to open api --
            file_path = os.path.join(img_download_folder, image_filie_name)
            image.download(file_path)

            (upload_url, object_id) = get_upload_url(file_path=file_path)
            logging.info("upload start")

            upload_file(file_path, upload_url)
            logging.info("upload finish")

            task_id = start_analysis(object_id, ai_model_parameter)
            logging.info("analysis start")

            image_str = f"{image_filie_name} ({i+1}/{nb_images})"
            job.update(
                status=Job.RUNNING, statusComment=f"progress: {progress} - Analyzing image id {image_id}, {image_str}...", progress=progress)
            logging.debug("Image id: %d width: %d height: %d resolution: %f magnification: %d filename: %s", image.id,
                          image.width, image.height, image.resolution, image.magnification, image.filename)

            while True:
                # TODO: progress UI에 슬라이드마다의 분석 진행 상황을 보여주고, 한 슬라이드가 끝나면 다시 0으로 돌어가도록 하자.
                analysis_status = get_analysis_status(task_id)
                print(analysis_status)
                if analysis_status in SUCCESS_STATUS:
                    break
                elif analysis_status in FAILED_STATUS:
                    raise Exception("Analysis Failed...")
                else:
                    time.sleep(5)
            result = get_analysis_result(task_id)

            # -- Add properties(aka. summary) to a Cytomine image --
            summary = result["summary"]

            for key, value in summary.items():
                prop = Property(image, key, value).save()
                logging.debug(prop)

            # -- draw ai analysis result contours as annotation --

            # Print the list of annotations in the given image:
            annotations = AnnotationCollection()
            annotations.image = image_id
            annotations.project = project_id
            annotations.showWKT = True
            annotations.showMeta = True
            annotations.showGIS = True
            annotations.showTerm = True
            annotations.fetch()
            print(annotations)

            # clean up older annotations(contours) rendering:
            for old_anno in annotations:
                old_anno.delete()

            slide = openslide.OpenSlide(file_path)
            slide_width, slide_height = slide.dimensions
            mpp, _ = get_mpp(slide)

            _heatmap_to_slide_ratio = 0.2465 * 16 / mpp
            heatmap_dicts = pd.read_pickle('/app/DB-002-015-01_heatmap_dicts.pkl') # result["heatmap"]["contours"][0]
            wkt_list = generate_wkt_list(
                'heatmap_dict', slide_height, _heatmap_to_slide_ratio, 'None', heatmap_dicts)

            for wkt, pattern in wkt_list:
                _term = pattern_term_key[pattern]
                if wkt.is_valid:
                    annotations.append(Annotation(location=str(
                        wkt), id_image=image_id, id_project=project_id, id_term=_term))
                else:
                    annotations.append(Annotation(location=str(wkt.buffer(
                        0)), id_image=image_id, id_project=project_id, id_term=_term))
            annotations.save()

            # # Print the list of annotations in the given image:
            # annotations = AnnotationCollection()
            # annotations.image = image_id
            # annotations.fetch()
            # print(annotations)

            # -- save ai analysis result as output.txt file --
            logging.info("Finished processing image %s", image_filie_name)
            progress += progress_delta

            output_path = os.path.join(
                working_path, f"{image_filie_name}output.txt")
            f = open(output_path, "w+")
            f.write(f"Input given was {ai_model_parameter}\r\n")
            f.write(json.dumps(result))
            f.close()

            # I save a file generated by this run into a "job data" that will be available in the UI.
            job_data = JobData(job.id, "Generated File",
                               f"{image_filie_name}output.txt").save()
            job_data.upload(output_path)

        job.update(status=Job.SUCCESS,
                   statusComment="progress: 100 - Done", progress=100)
    except Exception as e:
        print(e)
        job.update(status=Job.FAILED)
    finally:
        logging.info("Deleting folder %s", working_path)
        shutil.rmtree(working_path, ignore_errors=True)
        shutil.rmtree(img_download_folder, ignore_errors=True)

        logging.debug("Leaving run()")
        job.update(status=Job.TERMINATED)


if __name__ == "__main__":
    logging.debug("Command: %s", sys.argv)

    with cytomine.CytomineJob.from_cli(sys.argv) as cyto_job:
        run(cyto_job, cyto_job.parameters)
