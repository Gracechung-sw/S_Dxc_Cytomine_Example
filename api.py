import os
import requests
import json

#GCP openapi-test-env 프로젝트
PREFIX = "https://test-endpoints-nlnvnjcdbq-uc.a.run.app/api/v1"
RESUMABLE_UPLOAD_URL = f"{PREFIX}/images"
ANALYSIS_START_URL = f"{PREFIX}/analyses"
GET_ANALYSIS_STATUS_URL = f"{PREFIX}/analyses/status/"
GET_ANALYSIS_RESULT_URL = f"{PREFIX}/analyses/result/"

#업로드 URL 가져오기
def get_upload_url(file_path):   

    file_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)   
    res = requests.post(url=RESUMABLE_UPLOAD_URL, data={
        "size": file_size,
        "name": file_name,
        "lifetime": "once" 
    })   
    res.raise_for_status()
    response_data = res.json()    
    return (response_data['upload_url'], response_data['object_id'])


#파일 업로드 
def upload_file(file_path, url):
    file_size = os.path.getsize(file_path)
    with open(file=file_path, mode='rb') as f:
        index = 0
        while True:
            data = f.read(1024*1024*100)
            if not data:
                break
            headers = {
                "Content-Length": str(data.__len__()),
                "Content-Range": f"bytes {index}-{index + data.__len__() - 1}/{file_size}"}
            index += data.__len__()
            res = requests.put(url=url, headers=headers, data=data)
            res.raise_for_status()

#분석 시작 요청
def start_analysis(source, analysis_type):
    try:
        url = ANALYSIS_START_URL
        print(source)
        print(ANALYSIS_START_URL)
        res = requests.post(url=url, data={
            "source": source,
            "type": analysis_type
        })
        print(res.json())
        return res.json()["task_id"]
    except Exception as e:
        print(e)


#분석 상황 체크
def get_analysis_status(task_id):
    url = f"{GET_ANALYSIS_STATUS_URL}{task_id}"
    res = requests.get(url=url)
    res.raise_for_status()
    status = res.json()
    return status["statuses"][0]["status"]


#분석 결과 가져오기
def get_analysis_result(task_id):
    url = f"{GET_ANALYSIS_RESULT_URL}{task_id}"
    res = requests.get(url=url)
    res.raise_for_status()
    result = res.json()["results"][0]
    result_str = json.dumps(result)
    return result
