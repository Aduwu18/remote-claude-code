#!/bin/bash

cd "$(dirname "$0")"

if [ ! -f .pid ]; then
    echo "服务未运行"
    exit 0
fi

pid=$(cat .pid)

if ps -p $pid > /dev/null 2>&1; then
    kill $pid

    # 等待进程退出（最多 5 秒）
    for i in {1..10}; do
        if ! ps -p $pid > /dev/null 2>&1; then
            break
        fi
        sleep 0.5
    done

    # 如果进程仍在运行，强制终止
    if ps -p $pid > /dev/null 2>&1; then
        echo "进程未响应，强制终止..."
        kill -9 $pid
    fi

    rm -f .pid
    echo "服务已停止 (PID: $pid)"
else
    rm -f .pid
    echo "服务未运行"
fi