#!groovy

def dockerVersions = ['19.03.8']
//def baseImages = ['alpine', 'debian']
def baseImages = ['debian']
def pythonVersions = ['py37']

pipeline {
    agent none

    options {
        skipDefaultCheckout(true)
        buildDiscarder(logRotator(daysToKeepStr: '30'))
        timeout(time: 2, unit: 'HOURS')
        timestamps()
    }

    stages {
        stage('Build test images') {
            // TODO use declarative 1.5.0 `matrix` once available on CI
            parallel {
                /*stage('alpine') {
                    agent {
                        label 'master'
                    }
                    steps {
                        buildImage('alpine')
                    }
                }*/
                stage('debian') {
                    agent {
                        label 'master'
                    }
                    steps {
                        buildImage('debian')
                    }
                }
            }
        }
        stage('Test') {
            steps {
                // TODO use declarative 1.5.0 `matrix` once available on CI
                script {
                    def testMatrix = [:]
                    baseImages.each { baseImage ->
                       dockerVersions.each { dockerVersion ->
                        pythonVersions.each { pythonVersion ->
                          testMatrix["${baseImage}_${dockerVersion}_${pythonVersion}"] = runTests(dockerVersion, pythonVersion, baseImage)
                        }
                      }
                   }

                   parallel testMatrix
		    //testMatrix["${baseImage[0]}
                }
            }
        }
    }
}


def buildImage(baseImage) {
    def scmvar = checkout(scm)
    echo "scmvar is: ${scmvar}"
    def imageName = "jonjesse/compose:${baseImage}-${scmvar.GIT_COMMIT}"
    echo "imageName is ${imageName}"
    image = docker.image(imageName)
    echo "image is ${image}"
    
    docker.withRegistry('dockerbuildbot-index.docker.io') {    
        try {
            image.pull()
        } catch (exc) {
	  def custImage = docker.build("${imageName}","-f Dockerfile --target build --build-arg BUILD_PLATFORM=${baseImage} --build-arg GIT_COMMIT=${scmvar.GIT_COMMIT} .")
            //ansiColor('xterm') {
               // sh """docker build -t ${imageName} \\
                 //   --target build \\
                  //  --build-arg BUILD_PLATFORM="${baseImage}" \\
                   // --build-arg GIT_COMMIT="${scmvar.GIT_COMMIT}" \\
                   // .\\
               // """
                //sh "docker push ${imageName}"
            //}
            echo "Image built: ${imageName}"
	    echo "custImage: ${custImage}"
            return custImage
        }
    }
}

def runTests(dockerVersion, pythonVersion, baseImage) {
    return {
        stage("python=${pythonVersion} docker=${dockerVersion} ${baseImage}") {
              node("master") {
                def scmvar = checkout(scm)
                def imageName = "jonjesse/compose:${baseImage}-${scmvar.GIT_COMMIT}"
                def storageDriver = sh(script: "docker info -f \'{{.Driver}}\'", returnStdout: true).trim()
                echo "Using local system's storage driver: ${storageDriver}"
                docker.withRegistry('dockerbuildbot-index.docker.io') {
		  docker.image(imageName).withRun("-t --rm --privileged --volume='/home/jenkins/workspace/_docker_compose/.git:/code/.git' --volume='/var/run/docker.sock:/var/run/docker.sock' -e TAG=${imageName} -e 'STORAGE_DRIVER=${storageDriver}' -e 'DOCKER_VERSIONS=${dockerVersion}' -e 'PY_TEST_VERSIONS=${pythonVersion}' --entrypoint='script/test/ci'") {sh "./script/test/ci --verbose"}
		 //withDockerRegistry(credentialsId:'dockerbuildbot-index.docker.io') {
                  //  sh """docker run \\
                    //  -t \\
                   //   --rm \\
                   //   --privileged \\
                   //   --volume="/home/jenkins/workspace/_docker_compose/.git:/code/.git" \\
                   //   --volume="/var/run/docker.sock:/var/run/docker.sock" \\
                   //   -e "TAG=${imageName}" \\
                   //   -e "STORAGE_DRIVER=${storageDriver}" \\
                   //   -e "DOCKER_VERSIONS=${dockerVersion}" \\
                   //  -e "BUILD_NUMBER=${env.BUILD_NUMBER}" \\
                   //   -e "PY_TEST_VERSIONS=${pythonVersion}" \\
                   //   --entrypoint="script/test/ci" \\
                   //   ${imageName} \\
                   //   --verbose
                  //  """
                }
            }
        }
    }
}
