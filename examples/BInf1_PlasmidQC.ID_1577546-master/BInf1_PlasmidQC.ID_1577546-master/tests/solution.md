Команда fastqc
factqc SRR3341836.fastq.gz

Команда trimmomatic с параметрами из документации и SE
trimmomatic SE -phred33 SRR3341836.fastq.gz SRR3341836.trim.SE3.fastq.gz ILLUMINACLIP:TruSeq3-SE.fa:2:30:10 LEADING:3 TRAILING:3 SLIDINGWINDOW:4:15 MINLEN:36

Используемый файл адаптеров 
TruSeq3-SE.fa

GC-состав fasta1 (для решения делим суммарное количество G и C на длину последовательности) 
50,63%

GC-состав fasta2
44,87%

Выявленные программой fastqc проблемы до триммирования 

![solition1](images/solution1.png)

Выявленные программой fastqc проблемы после триммирования

![solition12](images/solution2.png)