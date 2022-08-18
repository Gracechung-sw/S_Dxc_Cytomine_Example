import os
import sys
import logging
import shutil
import time
import json

import openslide

import cytomine
from cytomine.models import ImageInstance, ImageInstanceCollection, Job, JobData, Property, Annotation, AnnotationCollection
from api import get_upload_url, start_analysis, upload_file, get_analysis_status, get_analysis_result
from contours import generate_wkt_from_openapi

SUCCESS_STATUS = ["FINISHED"]
FAILED_STATUS = ["DOWNLOAD_FAILED", "FAILED"]
PATTERN_TERM_KEY = {
    'Pattern3': [17243],
    'Pattern4': [17257],
    'Pattern5': [17285],
    'IDC-P': [17299],
    'Invasive': [17311],
    'DCIS': [17321],
    'Cancer': [17333]
    }


def parse_domain_list(s):
    if s is None or len(s) == 0:
        return []
    return list(map(int, s.split(',')))


def run(cyto_job, parameters):
    logging.info(f"Entering run(cyto_job={cyto_job}, parameters={parameters})")

    job = cyto_job.job
    project = cyto_job.project
    job_id = job.id
    project_id = project.id
    working_path = os.path.join("tmp", str(job_id))
    img_download_path = os.path.join("img", str(project_id))
    if not os.path.exists(working_path):
        logging.info(f"Creating working directory: {working_path}")
        os.makedirs(working_path)
    if not os.path.exists(img_download_path):
        logging.info(f"Creating img download directory: {img_download_path}")
        os.makedirs(img_download_path)

    try:
        ai_model_parameter = parameters.ai_model_type
        images_to_analyze = parameters.cytomine_id_images
        logging.info(f"Display ai_model_parameter {ai_model_parameter}")

        # -- Select images to process or Get list of images in my project by default
        images = ImageInstanceCollection()
        if images_to_analyze is not None:
            images_id = parse_domain_list(images_to_analyze)
            images.extend([ImageInstance().fetch(_id) for _id in images_id])
        else:
            images = images.fetch_with_filter("project", project_id)
        nb_images = len(images)
        logging.info(f"# images to analyze in this project: {nb_images}")

        # value between 0 and 100 that represent the progress bar displayed in the UI.
        progress = 0
        progress_delta = 100 / nb_images

        for (i, image) in enumerate(images):
            image_filie_name = image.instanceFilename
            image_id = image.id

            # -- ai analysis request to open api --
            file_path = os.path.join(img_download_path, image_filie_name)
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
            # TODO: Check if we use image.height without openslide
            logging.info(f"Image id: {image_id} width: {image.width} height: {image.height} resolution: {image.resolution} magnification: {image.magnification} filename: {image.filename}")

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
            analysis_result = get_analysis_result(task_id)

            # -- Add properties(aka. summary) to a Cytomine image --
            analysis_result_summary = analysis_result["summary"]

            for key, value in analysis_result_summary.items():
                prop = Property(image, key, value).save()
                logging.debug(prop)

            # -- draw ai analysis result contours as annotation --
            annotations = AnnotationCollection()
            annotations.image = image_id
            annotations.project = project_id
            annotations.showWKT = True
            annotations.showMeta = True
            annotations.showGIS = True
            annotations.showTerm = True
            annotations.fetch()

            # clean up old contours annotations
            for old_anno in annotations:
                old_anno.delete()

            slide = openslide.OpenSlide(file_path)
            slide_width, slide_height = slide.dimensions

            wkt_list = generate_wkt_from_openapi(analysis_result, slide_height)

            for wkt, pattern in wkt_list:
                _term = PATTERN_TERM_KEY[pattern]
                if wkt.is_valid:
                    annotations.append(Annotation(location=str(
                        wkt), id_image=image_id, id_project=project_id, id_term=_term))
                else:
                    annotations.append(Annotation(location=str(wkt.buffer(
                        0)), id_image=image_id, id_project=project_id, id_term=_term))
            annotations.save()

            # -- save ai analysis result as output.txt file --
            logging.info(f"Finished processing image {image_filie_name}")
            progress += progress_delta

            output_path = os.path.join(
                working_path, f"{image_filie_name}output.txt")
            f = open(output_path, "w+")
            f.write(f"Analysis Model type: {ai_model_parameter}\r\n")
            f.write(json.dumps(analysis_result))
            f.close()

            # -- upload output.txt file to cytomine --
            job_data = JobData(job_id, "Generated File",
                               f"{image_filie_name}output.txt").save()
            job_data.upload(output_path)

        job.update(status=Job.SUCCESS,
                   statusComment="progress: 100 - Done", progress=100)
    except Exception as e:
        print(e)
        job.update(status=Job.FAILED)
    finally:
        logging.info(f"Deleting folder {working_path}")
        shutil.rmtree(working_path, ignore_errors=True)
        shutil.rmtree(img_download_path, ignore_errors=True)

        logging.debug("Leaving run()")
        job.update(status=Job.TERMINATED)


if __name__ == "__main__":
    logging.debug(f"Command: {sys.argv}")
    with cytomine.CytomineJob.from_cli(sys.argv) as cyto_job:
        run(cyto_job, cyto_job.parameters)
