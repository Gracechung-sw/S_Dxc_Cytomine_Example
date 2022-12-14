# * Copyright (c) 2009-2020. Authors: see NOTICE file.
# *
# * Licensed under the Apache License, Version 2.0 (the "License");
# * you may not use this file except in compliance with the License.
# * You may obtain a copy of the License at
# *
# *      http://www.apache.org/licenses/LICENSE-2.0
# *
# * Unless required by applicable law or agreed to in writing, software
# * distributed under the License is distributed on an "AS IS" BASIS,
# * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# * See the License for the specific language governing permissions and
# * limitations under the License.

FROM cytomine/software-python3-base

# Install openslide.
RUN apt-get -qq install -y openslide-tools

# Install build tools for openslide-python
RUN pip install -U 'setuptools==45.2.0'
RUN apt-get -qq install -y build-essential
RUN pip install wheel

RUN pip install openslide-python

WORKDIR /app

COPY . .

ENTRYPOINT ["python", "/app/main.py"]
