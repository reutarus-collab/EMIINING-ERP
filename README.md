git clone https://github.com/reutarus-collab/EMIINING-ERP.git
cp -r emining-erp/* EMIINING-ERP/
cp emining-erp/.gitignore EMIINING-ERP/ 2>/dev/null
cd EMIINING-ERP
git add .
git commit -m "Initial ERP scaffold"
git push
