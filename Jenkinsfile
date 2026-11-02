// Tests, builds and scans this repository. Nothing is published, and no credentials are used.
// See docs/M8_DOCKER_JENKINS_TRIVY.md. Every step runs on the CI Docker daemon, never the host's.

def inPython(String command) {
    sh """docker run --rm --label ci=troubleshooting-agent -u 1000:1000 -e HOME=/tmp \
        -e PIP_CACHE_DIR=/pip -e PIP_DISABLE_PIP_VERSION_CHECK=1 -v ci-pip-cache:/pip \
        -v "\$WORKSPACE:/w" -w /w/backend "\$PYTHON_IMAGE" sh -c '${command}'"""
}

def inNode(String command) {
    sh """docker run --rm --label ci=troubleshooting-agent -u 1000:1000 -e HOME=/tmp \
        -e npm_config_cache=/npm -v ci-npm-cache:/npm \
        -v "\$WORKSPACE:/w" -w /w/frontend "\$NODE_IMAGE" sh -c '${command}'"""
}

pipeline {
    agent any

    options {
        disableConcurrentBuilds()
        timeout(time: 45, unit: 'MINUTES')
        skipDefaultCheckout(true)
    }

    environment {
        PYTHON_IMAGE = 'python:3.13-slim@sha256:ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1'
        NODE_IMAGE = 'node:22-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914'
        KUBECONFORM_IMAGE = 'ghcr.io/yannh/kubeconform:latest@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e'
        TRIVY_IMAGE = 'aquasec/trivy:0.76.1@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c'
        BACKEND_IMAGE = 'troubleshooting-agent-backend'
        FRONTEND_IMAGE = 'troubleshooting-agent-frontend'
    }

    stages {
        stage('Checkout') {
            steps {
                deleteDir()
                checkout scm
                sh 'mkdir -p reports .ci'
                script {
                    env.IMAGE_TAG = sh(returnStdout: true, script: 'git rev-parse --short=12 HEAD').trim()
                }
                sh 'git log -1 --format="Building %h: %s"'
            }
        }

        stage('Backend dependencies') {
            options { timeout(time: 10, unit: 'MINUTES') }
            steps {
                sh 'docker run --rm -v ci-pip-cache:/pip "$PYTHON_IMAGE" chown 1000:1000 /pip'
                inPython('python -m venv /w/.ci/venv && /w/.ci/venv/bin/pip install -q -r requirements-dev.txt')
            }
        }

        stage('Backend lint') {
            options { timeout(time: 5, unit: 'MINUTES') }
            steps {
                inPython('/w/.ci/venv/bin/ruff check --no-cache app tests ../scripts')
            }
        }

        stage('Backend tests') {
            options { timeout(time: 15, unit: 'MINUTES') }
            steps {
                // The default markers leave out cluster tests: Jenkins has no cluster access, by design.
                inPython('/w/.ci/venv/bin/python -m pytest -q -p no:cacheprovider --junitxml=/w/reports/backend-tests.xml')
            }
        }

        stage('Frontend lint') {
            options { timeout(time: 10, unit: 'MINUTES') }
            steps {
                sh 'docker run --rm -v ci-npm-cache:/npm "$NODE_IMAGE" chown 1000:1000 /npm'
                inNode('npm ci --no-audit --no-fund && npm run lint -- --deny-warnings')
            }
        }

        stage('Frontend build') {
            options { timeout(time: 5, unit: 'MINUTES') }
            steps {
                inNode('npm run build')
            }
        }

        stage('Validate manifests') {
            options { timeout(time: 5, unit: 'MINUTES') }
            steps {
                sh '''
                    if ! docker run --rm -v "$WORKSPACE/k8s:/k8s:ro" "$KUBECONFORM_IMAGE" -strict -summary \
                        -kubernetes-version 1.37.0 /k8s/base /k8s/rbac /k8s/scenarios > reports/kubeconform.txt 2>&1; then
                        cat reports/kubeconform.txt
                        exit 1
                    fi
                    cat reports/kubeconform.txt
                '''
            }
        }

        stage('Build images') {
            options { timeout(time: 15, unit: 'MINUTES') }
            steps {
                sh '''
                    docker build --label "commit=$IMAGE_TAG" -t "$BACKEND_IMAGE:$IMAGE_TAG" backend
                    docker build --label "commit=$IMAGE_TAG" -t "$FRONTEND_IMAGE:$IMAGE_TAG" frontend
                    docker images --filter label=project=troubleshooting-agent | tee reports/images.txt
                '''
            }
        }

        stage('Smoke test') {
            options { timeout(time: 5, unit: 'MINUTES') }
            steps {
                sh 'sh ci/smoke-test.sh "$BACKEND_IMAGE:$IMAGE_TAG" "$FRONTEND_IMAGE:$IMAGE_TAG"'
            }
        }

        stage('Trivy') {
            options { timeout(time: 20, unit: 'MINUTES') }
            steps {
                sh 'sh ci/trivy-scan.sh "$BACKEND_IMAGE:$IMAGE_TAG" "$FRONTEND_IMAGE:$IMAGE_TAG"'
            }
        }
    }

    post {
        always {
            junit testResults: 'reports/*.xml', allowEmptyResults: true
            archiveArtifacts artifacts: 'reports/**', allowEmptyArchive: true
            // Scoped to this project's label, and on the CI daemon only. Never a global prune.
            sh '''
                docker ps -aq --filter label=ci=troubleshooting-agent | xargs -r docker rm -f
                rm -rf .ci
            '''
        }
        failure {
            // An image that did not pass every check must not be left looking usable. Its layers
            // stay as build cache, so the next build does not download everything again.
            sh '''
                if [ -n "${IMAGE_TAG:-}" ]; then
                    docker image rm -f --no-prune "$BACKEND_IMAGE:$IMAGE_TAG" "$FRONTEND_IMAGE:$IMAGE_TAG" 2>/dev/null || true
                fi
            '''
        }
    }
}
