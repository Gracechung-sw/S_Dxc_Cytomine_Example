import os
import sys
import logging
import shutil
import time
import json
import uuid

from shapely.geometry import Point, box

import cytomine
from cytomine.models import ImageInstance, ImageInstanceCollection, Job, JobData, Property, Annotation, AnnotationTerm, AnnotationCollection

from api import get_upload_url, start_analysis, upload_file, get_analysis_status, get_analysis_result

SUCCESS_STATUS = ["FINISHED"]
FAILED_STATUS = ["DOWNLOAD_FAILED", "FAILED"]


def run(cyto_job, parameters):
    logging.info("Entering run(cyto_job=%s, parameters=%s)", cyto_job, parameters)

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
        logging.info("Creating img download directory: %s", img_download_folder)
        os.makedirs(img_download_folder)

    try:
        ai_model_parameter = parameters.ai_model_type

        logging.info("Display ai_model_parameter %s", ai_model_parameter)

        # loop for images in the project
        # Select images to process
        # Get list of images im my project
        
        images = ImageInstanceCollection().fetch_with_filter("project", project_id)
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

            task_id = start_analysis(object_id)
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
            # We first add a point in (10, 10) where (0, 0) is bottom-left corner.
            point = Point(10, 10)
            annotation_point = Annotation(
                location=point.wkt, id_image=image_id).save()

            # Then, we add a rectangle as annotation
            rectangle = box(20, 20, 100, 100)
            annotation_rectangle = Annotation(
                location=rectangle.wkt, id_image=image_id).save()

            # We can also add a property (key-value pair) to an annotation
            Property(annotation_rectangle, key="my_property", value=10).save()

            # Print the list of annotations in the given image:
            annotations = AnnotationCollection()
            annotations.image = image_id
            annotations.fetch()
            print(annotations)

            # We can also add multiple annotation in one request:
            annotations = AnnotationCollection()
            annotations.append(Annotation(
                location=point.wkt, id_image=image_id, id_project=project_id))
            annotations.append(Annotation(
                location=rectangle.wkt, id_image=image_id, id_project=project_id))
            annotations.save()

            # Print the list of annotations in the given image:
            annotations = AnnotationCollection()
            annotations.image = image_id
            annotations.fetch()
            print(annotations)
            
            # -- save ai analysis result as output.txt file --
            logging.info("Finished processing image %s",image_filie_name)
            progress += progress_delta

            output_path = os.path.join(working_path, f"{image_filie_name}output.txt")
            f = open(output_path, "w+")
            f.write(f"Input given was {ai_model_parameter}\r\n")
            f.write(json.dumps(result))
            f.close()

            # I save a file generated by this run into a "job data" that will be available in the UI.
            job_data = JobData(job.id, "Generated File", f"{image_filie_name}output.txt").save()
            job_data.upload(output_path)

        job.update(status=Job.SUCCESS, statusComment="progress: 100 - Done", progress=100)
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
