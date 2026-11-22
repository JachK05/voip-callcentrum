FROM alpine:3.22 AS mereni
RUN apk add --no-cache asterisk asterisk-sample-config python3
COPY *.conf /etc/asterisk/
COPY sounds/ /var/lib/asterisk/sounds/en/custom/
COPY mer.py /lab/mer.py
RUN mkdir -p /run/asterisk /var/log/asterisk /var/spool/asterisk /var/lib/asterisk /pokusy
CMD ["asterisk", "-f", "-n", "-vvv"]

FROM mereni AS sip
RUN apk add --no-cache tcpdump
COPY sip/*.conf /etc/asterisk/
COPY sip/prijato.wav /var/lib/asterisk/sounds/en/custom/
RUN printf '\n#include modules-sip.conf\n' >> /etc/asterisk/modules.conf && \
    printf '\n#include extensions-sip.conf\n' >> /etc/asterisk/extensions.conf && \
    printf '\n#include queues-sip.conf\n' >> /etc/asterisk/queues.conf

FROM mereni AS vychozi
